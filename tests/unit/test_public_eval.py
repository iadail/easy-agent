import httpx

from agent_config.app import AppConfig
from agent_runtime.public_eval import (
    _build_tool_name_map,
    _is_retryable_provider_400,
    _normalize_schema,
    _score_bfcl_case,
    _score_tau_case,
    _select_bfcl_candidate_functions,
    _strict_normalize_schema,
)


def test_score_bfcl_case_accepts_exact_match() -> None:
    case = {
        'expect_no_tool': False,
        'ground_truth': [{'math.gcd': {'num1': [12], 'num2': [18]}}],
    }
    actual_calls = [{'name': 'math.gcd', 'arguments': {'num1': 12, 'num2': 18}}]

    success, tool_match, arg_match = _score_bfcl_case(case, actual_calls)

    assert success is True
    assert tool_match == 1.0
    assert arg_match == 1.0



def test_score_bfcl_case_handles_irrelevance() -> None:
    case = {'expect_no_tool': True, 'ground_truth': []}

    success, tool_match, arg_match = _score_bfcl_case(case, [])

    assert success is True
    assert tool_match == 1.0
    assert arg_match == 1.0



def test_score_tau_case_requires_expected_action() -> None:
    case = {
        'evaluation_criteria': {
            'actions': [{'name': 'update_task_status', 'arguments': {'task_id': 'task_1', 'status': 'completed'}}]
        }
    }
    actual_calls = [{'name': 'update_task_status', 'arguments': {'task_id': 'task_1', 'status': 'completed'}}]

    success, tool_match, arg_match = _score_tau_case(case, actual_calls)

    assert success is True
    assert tool_match == 1.0
    assert arg_match == 1.0



def test_build_tool_name_map_sanitizes_bfcl_function_names() -> None:
    mapping = _build_tool_name_map([
        {'name': 'math.factorial'},
        {'name': 'math/factorial'},
    ])

    assert mapping['math.factorial'] == 'math_factorial'
    assert mapping['math/factorial'] == 'math_factorial_2'



def test_normalize_schema_converts_non_openai_json_types() -> None:
    schema = _normalize_schema(
        {
            'type': 'dict',
            'properties': {
                'items': {
                    'type': 'tuple',
                    'items': {'type': 'dict', 'properties': {'count': {'type': 'integer'}}},
                },
                'rating': {'type': 'float', 'optional': True},
            },
        }
    )

    assert schema['type'] == 'object'
    assert schema['properties']['items']['type'] == 'array'
    assert schema['properties']['items']['items']['type'] == 'object'
    assert schema['properties']['rating']['type'] == 'number'
    assert 'optional' not in schema['properties']['rating']



def test_strict_normalize_schema_drops_non_core_fields() -> None:
    schema = _strict_normalize_schema(
        {
            'type': 'dict',
            'description': 'root',
            'properties': {
                'when': {'type': 'string', 'description': 'When', 'format': 'date-time'},
            },
            'required': ['when'],
            'additionalProperties': False,
        }
    )

    assert schema == {
        'type': 'object',
        'properties': {'when': {'type': 'string'}},
        'required': ['when'],
    }



def test_select_bfcl_candidate_functions_prunes_irrelevant_tools() -> None:
    prompt = 'Calculate the area of a triangle given the base is 10 meters and height is 5 meters.'
    functions = [
        {
            'name': 'determine_body_mass_index',
            'description': 'Calculate body mass index given weight and height.',
            'parameters': {'type': 'dict', 'properties': {'weight': {'type': 'float'}, 'height': {'type': 'float'}}},
        }
    ]

    assert _select_bfcl_candidate_functions(prompt, functions) == []



def test_select_bfcl_candidate_functions_keeps_multiple_high_relevance_tools() -> None:
    prompt = 'Find the area of a rectangle with length 7 and breadth 3. Also, calculate the area of a circle with radius 5.'
    functions = [
        {
            'name': 'volume_cylinder.calculate',
            'description': 'Calculate the volume of a cylinder given the radius and the height.',
            'parameters': {'type': 'dict', 'properties': {'radius': {'type': 'float'}, 'height': {'type': 'float'}}},
        },
        {
            'name': 'area_rectangle.calculate',
            'description': 'Calculate the area of a rectangle given the length and breadth.',
            'parameters': {'type': 'dict', 'properties': {'length': {'type': 'float'}, 'breadth': {'type': 'float'}}},
        },
        {
            'name': 'area_circle.calculate',
            'description': 'Calculate the area of a circle given the radius.',
            'parameters': {'type': 'dict', 'properties': {'radius': {'type': 'float'}}},
        },
    ]

    selected = _select_bfcl_candidate_functions(prompt, functions)

    assert [item['name'] for item in selected] == ['area_rectangle.calculate', 'area_circle.calculate']



def test_retryable_provider_400_checks_openai_compatible_provider() -> None:
    request = httpx.Request('POST', 'https://api.deepseek.com/chat/completions')
    response = httpx.Response(400, request=request)
    exc = httpx.HTTPStatusError('bad request', request=request, response=response)
    deepseek_config = AppConfig.model_validate(
        {'model': {'provider': 'deepseek'}, 'graph': {'entrypoint': 'coordinator', 'agents': [{'name': 'coordinator'}]}}
    )
    anthropic_config = AppConfig.model_validate(
        {'model': {'provider': 'anthropic'}, 'graph': {'entrypoint': 'coordinator', 'agents': [{'name': 'coordinator'}]}}
    )

    assert _is_retryable_provider_400(deepseek_config, exc) is True
    assert _is_retryable_provider_400(anthropic_config, exc) is False

