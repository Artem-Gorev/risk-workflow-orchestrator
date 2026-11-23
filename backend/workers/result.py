from decimal import Decimal, InvalidOperation


def decimal_string(value):
    if not isinstance(value, str):
        return False
    try:
        number = Decimal(value)
        return number.is_finite() and number.as_tuple().exponent == -2
    except InvalidOperation:
        return False


def valid_output(resource_key, value):
    if resource_key == "risk_parameters":
        return (
            isinstance(value, dict)
            and set(value) == {"creditFactor", "marketFactor"}
            and all(decimal_string(factor) for factor in value.values())
        )
    return decimal_string(value)
