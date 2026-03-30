from __future__ import annotations

from typing import Any

_TYPE_ALIASES = {
    'bool': 'boolean',
    'boolean': 'boolean',
    'dict': 'object',
    'double': 'number',
    'float': 'number',
    'int': 'integer',
    'integer': 'integer',
    'list': 'array',
    'map': 'object',
    'number': 'number',
    'object': 'object',
    'str': 'string',
    'string': 'string',
    'tuple': 'array',
    'array': 'array',
    'decimal': 'number',
    'null': 'null',
}
_NOISY_KEYS = {
    '$defs',
    '$schema',
    'default',
    'definitions',
    'examples',
    'format',
    'nullable',
    'optional',
    'title',
}
_CORE_KEYS = {'items', 'properties', 'required', 'type'}


def normalize_json_schema(
    schema: dict[str, Any],
    *,
    drop_descriptions: bool = False,
    core_only: bool = False,
) -> dict[str, Any]:
    normalized = _normalize_schema_dict(schema, drop_descriptions=drop_descriptions, core_only=core_only)
    return normalized if normalized else {'type': 'object'}


def _normalize_schema_dict(
    schema: dict[str, Any],
    *,
    drop_descriptions: bool,
    core_only: bool,
) -> dict[str, Any]:
    normalized = dict(schema)
    collapsed = _collapse_variants(normalized, drop_descriptions=drop_descriptions, core_only=core_only)
    if collapsed is not None:
        normalized = collapsed
    for key in _NOISY_KEYS:
        normalized.pop(key, None)
    schema_type = _infer_schema_type(normalized)
    normalized['type'] = schema_type
    if schema_type == 'object':
        raw_properties = normalized.get('properties')
        properties = raw_properties if isinstance(raw_properties, dict) else {}
        safe_properties: dict[str, Any] = {}
        for key, value in properties.items():
            if isinstance(value, dict):
                safe_properties[key] = _normalize_schema_dict(
                    value,
                    drop_descriptions=drop_descriptions,
                    core_only=core_only,
                )
            else:
                safe_properties[key] = {'type': _normalize_json_type(value)}
        normalized['properties'] = safe_properties
        required = normalized.get('required')
        if isinstance(required, list):
            normalized['required'] = [str(item) for item in required if str(item) in safe_properties]
        else:
            normalized.pop('required', None)
        additional_properties = normalized.get('additionalProperties')
        if isinstance(additional_properties, bool):
            normalized['additionalProperties'] = additional_properties
        else:
            normalized.pop('additionalProperties', None)
    elif schema_type == 'array':
        raw_items = normalized.get('items')
        if isinstance(raw_items, dict):
            normalized['items'] = _normalize_schema_dict(
                raw_items,
                drop_descriptions=drop_descriptions,
                core_only=core_only,
            )
        else:
            normalized['items'] = {'type': _normalize_json_type(raw_items)}
    else:
        normalized.pop('properties', None)
        normalized.pop('required', None)
        normalized.pop('items', None)
        normalized.pop('additionalProperties', None)
    if drop_descriptions:
        normalized.pop('description', None)
    elif 'description' in normalized and not isinstance(normalized['description'], str):
        normalized.pop('description', None)
    if core_only:
        normalized = {key: value for key, value in normalized.items() if key in _CORE_KEYS}
    return normalized


def _collapse_variants(
    schema: dict[str, Any],
    *,
    drop_descriptions: bool,
    core_only: bool,
) -> dict[str, Any] | None:
    for key in ('anyOf', 'oneOf', 'allOf'):
        options = schema.get(key)
        if not isinstance(options, list) or not options:
            continue
        normalized_options = [
            _normalize_schema_dict(item, drop_descriptions=drop_descriptions, core_only=core_only)
            for item in options
            if isinstance(item, dict)
        ]
        non_null = [item for item in normalized_options if item.get('type') != 'null']
        if len(non_null) == 1:
            selected = dict(non_null[0])
        else:
            candidate_types = {str(item.get('type', 'string')) for item in non_null}
            if candidate_types.issubset({'integer', 'number'}):
                selected = {'type': 'number'}
            elif candidate_types == {'boolean'}:
                selected = {'type': 'boolean'}
            elif candidate_types == {'array'}:
                selected = non_null[0] if non_null else {'type': 'array', 'items': {'type': 'string'}}
            elif candidate_types == {'object'}:
                selected = non_null[0] if non_null else {'type': 'object', 'properties': {}}
            elif 'string' in candidate_types:
                selected = {'type': 'string'}
            elif non_null:
                selected = {'type': str(non_null[0].get('type', 'string'))}
            else:
                selected = {'type': 'string'}
        description = schema.get('description')
        if not drop_descriptions and isinstance(description, str) and 'description' not in selected:
            selected['description'] = description
        return selected
    return None


def _infer_schema_type(schema: dict[str, Any]) -> str:
    if 'type' in schema:
        return _normalize_json_type(schema.get('type'))
    if 'properties' in schema or 'required' in schema:
        return 'object'
    if 'items' in schema:
        return 'array'
    return 'object'


def _normalize_json_type(value: Any) -> str:
    if isinstance(value, list):
        normalized = [_normalize_json_type(item) for item in value]
        non_null = [item for item in normalized if item != 'null']
        if len(non_null) == 1:
            return non_null[0]
        if set(non_null).issubset({'integer', 'number'}):
            return 'number'
        return non_null[0] if non_null else 'string'
    schema_type = str(value or 'object').strip().lower()
    return _TYPE_ALIASES.get(schema_type, schema_type or 'object')
