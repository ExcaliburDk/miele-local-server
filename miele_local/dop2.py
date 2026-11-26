"""DOP2 binary payload parser for Miele@home devices."""

import binascii
import logging
import struct
from enum import Enum
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class MieleIntegerFormat(str, Enum):
    Unsigned = "U"
    Signed = "S"
    E = "E"


class MieleAttribute:
    """Decoded DOP2 attribute field."""

    def __init__(self, wire_length: int, type_name: str, value: Any) -> None:
        self.wireLength = wire_length
        self.typeName = type_name
        self.value = value

    def to_json_value(self) -> Any:
        if isinstance(self.value, bytes):
            return binascii.hexlify(self.value).decode("ascii")
        if isinstance(self.value, list):
            return [
                x.to_json_value() if isinstance(x, MieleAttribute) else str(x)
                for x in self.value
            ]
        return self.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "wireLength": self.wireLength,
            "typeName": self.typeName,
            "value": self.to_json_value(),
        }

    def __str__(self) -> str:
        if isinstance(self.value, list):
            value_str = [str(x) for x in self.value]
        else:
            value_str = str(self.value)
        return f"{self.typeName}={value_str}"


class EmptyMieleAttribute(MieleAttribute):
    def __init__(self) -> None:
        super().__init__(0, "empty", "")


class MieleAttributeDecoder:
    def __init__(self, byte_length: int, type_name: str) -> None:
        self.byteLength = byte_length
        self.typeName = type_name

    def __str__(self) -> str:
        return self.typeName


class MieleFixedLengthAttributeDecoder(MieleAttributeDecoder):
    def decode(self, payload: bytes) -> MieleAttribute:
        if len(payload) < self.byteLength:
            raise ValueError(
                f"Insufficient bytes for {self.typeName}: need {self.byteLength}, got {len(payload)}"
            )
        return MieleAttribute(
            self.byteLength,
            self.typeName,
            self.fixed_length_decode(payload[0 : self.byteLength]),
        )

    def fixed_length_decode(self, data: bytes) -> Any:
        raise NotImplementedError


class MieleVariableLengthAttributeDecoder(MieleAttributeDecoder):
    def __init__(self, type_name: str) -> None:
        super().__init__(0, type_name)

    def decode(self, remaining_payload: bytes) -> MieleAttribute:
        wire_length, values = self.variable_length_decode(remaining_payload)
        return MieleAttribute(wire_length, self.typeName, values)

    def variable_length_decode(self, remaining_payload: bytes):
        raise NotImplementedError


class MieleBool(MieleFixedLengthAttributeDecoder):
    def __init__(self) -> None:
        super().__init__(1, "bool")

    def fixed_length_decode(self, payload: bytes) -> bool:
        val = int.from_bytes(payload, "big")
        if val == 0x01:
            return True
        if val == 0x00:
            return False
        raise ValueError(f"DOP2 Boolean not 0x01 or 0x00 but {val}")


class MieleInteger(MieleFixedLengthAttributeDecoder):
    def __init__(self, byte_length: int, integer_format: MieleIntegerFormat) -> None:
        super().__init__(byte_length, f"{integer_format.value}{byte_length * 8}")
        self.signed = integer_format == MieleIntegerFormat.Signed

    def fixed_length_decode(self, data: bytes) -> int:
        return int.from_bytes(data, byteorder="big", signed=self.signed)


class MieleFloat(MieleFixedLengthAttributeDecoder):
    def __init__(self, byte_length: int) -> None:
        super().__init__(byte_length, f"float{byte_length * 8}")

    def fixed_length_decode(self, data: bytes) -> float:
        fmt = "<f" if self.byteLength == 4 else "<d"
        return struct.unpack(fmt, data)[0]


class MieleString(MieleVariableLengthAttributeDecoder):
    def __init__(self) -> None:
        super().__init__("string")

    def variable_length_decode(self, remaining_payload: bytes):
        if len(remaining_payload) < 2:
            raise ValueError("Truncated string header")
        header_length = 2
        string_length = (remaining_payload[0] << 8) + remaining_payload[1]
        wire_length = header_length + string_length
        if len(remaining_payload) < wire_length:
            raise ValueError(
                f"Truncated string payload: need {wire_length}, got {len(remaining_payload)}"
            )
        return wire_length, bytes(remaining_payload[header_length:wire_length])


class MieleArray(MieleVariableLengthAttributeDecoder):
    def __init__(self, element_decoder: MieleAttributeDecoder) -> None:
        self.elementDecoder = element_decoder
        super().__init__(element_decoder.typeName + "[]")

    def decode(self, data: bytes) -> MieleAttribute:
        if len(data) < 2:
            raise ValueError("Truncated array header")
        number_elements = (data[0] << 8) + data[1]
        elements = []
        element_wire_length = 0
        cursor_data = data
        while len(elements) < number_elements:
            if len(cursor_data) < 3:
                raise ValueError(
                    f"Truncated array element {len(elements) + 1} of {number_elements}"
                )
            new_element = self.elementDecoder.decode(cursor_data[2:])
            cursor_data = cursor_data[2 + new_element.wireLength :]
            element_wire_length += new_element.wireLength
            elements.append(new_element)
        total_wire_length = element_wire_length + 2
        return MieleAttribute(total_wire_length, self.typeName, elements)


class MieleStruct(MieleAttributeDecoder):
    def __init__(self) -> None:
        super().__init__(0, "struct")

    def decode(self, data: bytes) -> MieleAttribute:
        if len(data) < 3:
            raise ValueError("Truncated struct header")
        decoder = MieleAttributeParser()
        fields: List[MieleAttribute] = []
        field_length = 0
        _, number_of_fields, _ = data[0:3]
        if number_of_fields == 0 or number_of_fields > 160:
            raise ValueError(f"Empty or implausibly large struct ({number_of_fields} fields)")
        cursor_data = data[3:]
        while True:
            if len(cursor_data) < 2:
                raise ValueError("Truncated struct field header")
            data_type = cursor_data[1]
            field = decoder.parse_field(data_type, cursor_data[2:])
            current_field_length = field.wireLength + 2
            field_length += current_field_length
            fields.append(field)
            if len(fields) == number_of_fields:
                break
            cursor_data = cursor_data[current_field_length:]
            if cursor_data and cursor_data[0] == 0x00:
                cursor_data = cursor_data[1:]
                field_length += 1

        return MieleAttribute(field_length + 3, "struct", fields)


class MieleAStruct(MieleVariableLengthAttributeDecoder):
    def __init__(self) -> None:
        super().__init__("A_struct[]")

    def variable_length_decode(self, remaining_payload: bytes):
        if len(remaining_payload) < 2:
            raise ValueError("Truncated A_struct header")
        number_of_fields = (remaining_payload[0] << 8) + remaining_payload[1]
        if number_of_fields == 0:
            raise ValueError("Cannot have empty A_struct")
        rest = remaining_payload[2:]
        if len(rest) < 5:
            raise ValueError("Truncated A_struct entry")
        current_struct = MieleAttributeParser().parse_bytes(rest[5:])
        return 5, [current_struct]


class MieleAttributeParser:
    """Parser for Miele DOP2 binary attribute payloads."""

    def __init__(self) -> None:
        self.decoders: Dict[int, MieleAttributeDecoder] = {
            1: MieleBool(),
            2: MieleInteger(1, MieleIntegerFormat.Unsigned),
            3: MieleInteger(1, MieleIntegerFormat.Signed),
            4: MieleInteger(1, MieleIntegerFormat.E),
            5: MieleInteger(2, MieleIntegerFormat.Unsigned),
            6: MieleInteger(2, MieleIntegerFormat.Signed),
            7: MieleInteger(2, MieleIntegerFormat.E),
            8: MieleInteger(4, MieleIntegerFormat.Unsigned),
            9: MieleInteger(4, MieleIntegerFormat.Signed),
            10: MieleInteger(4, MieleIntegerFormat.E),
            11: MieleInteger(8, MieleIntegerFormat.Unsigned),
            12: MieleInteger(8, MieleIntegerFormat.Signed),
            13: MieleInteger(8, MieleIntegerFormat.E),
            14: MieleFloat(4),
            15: MieleFloat(8),
            16: MieleStruct(),
            17: MieleArray(MieleBool()),
            18: MieleString(),
            20: MieleArray(MieleInteger(1, MieleIntegerFormat.E)),
            21: MieleArray(MieleInteger(2, MieleIntegerFormat.Unsigned)),
            22: MieleArray(MieleInteger(2, MieleIntegerFormat.Signed)),
            23: MieleArray(MieleInteger(2, MieleIntegerFormat.E)),
            25: MieleArray(MieleInteger(4, MieleIntegerFormat.Signed)),
            27: MieleArray(MieleInteger(8, MieleIntegerFormat.Unsigned)),
            32: MieleString(),
            33: MieleAStruct(),
        }

    def parse_field(self, field_type: int, data: bytes) -> MieleAttribute:
        if field_type not in self.decoders:
            raise ValueError(f"Unsupported DOP2 field type {field_type}")
        return self.decoders[field_type].decode(data)

    def parse_bytes(self, response: bytes) -> List[Any]:
        if not response or len(response) < 8:
            return []
        payload_length = response[1] + (response[0] << 8)
        padding_bytes_expected = max(0, len(response) - payload_length - 2)
        payload = response[8 : len(response) - padding_bytes_expected]
        if len(payload) < 5:
            return []

        number_of_fields = payload[3] + (payload[4] << 8)
        if number_of_fields == 0:
            return []

        fields: List[Any] = []
        remaining_payload = payload[5:]
        try:
            while len(remaining_payload) >= 2:
                expected_index = len(fields) + 1
                actual_index = remaining_payload[0]
                if expected_index != actual_index:
                    if expected_index > actual_index:
                        raise ValueError("field index regressed")
                    while len(fields) + 1 < actual_index:
                        fields.append(EmptyMieleAttribute())
                field_type = remaining_payload[1]
                field_value = self.parse_field(field_type, remaining_payload[2:])
                if field_value.wireLength == 0:
                    raise ValueError("zero-length field not possible")
                fields.append(field_value)
                if len(fields) >= number_of_fields:
                    break
                remaining_payload = remaining_payload[3 + field_value.wireLength :]
        except Exception as exc:
            logger.debug("Partial DOP2 parse: %s", exc)
            fields.append(
                f"partial parse ({len(fields)}/{number_of_fields} fields): {exc}"
            )
        return fields

    # Alias for backward compatibility
    parseBytes = parse_bytes
