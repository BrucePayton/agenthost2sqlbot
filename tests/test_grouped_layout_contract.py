from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.agui.contracts import load_contract_registry


@pytest.fixture
def layout_contract():
    root = Path(__file__).resolve().parents[1]
    return load_contract_registry(root / 'contracts/davinci-agent-v2.json').get(
        'dashboard.set_widget_layout'
    )


def regroup_request():
    """Use promoted children, including a hidden Tab child, as effective roots."""
    return {
        'preset': {
            'mode': 'reorder',
            'sizing': 'content',
            'orderedWidgetIds': ['metric', 'flat-child', 'hidden-tab-child'],
            'groups': [
                {'title': 'Summary', 'widgetIds': ['metric', 'flat-child', 'hidden-tab-child']},
            ],
            'groupingConfirmed': True,
            'regroup': {'containerWidgetIds': ['old-flat', 'old-tab'], 'confirmed': True},
        },
        'expectedResourceRevision': 8,
    }


def test_comparison_pairs_are_optional_bounded_two_id_relations(layout_contract):
    request = regroup_request()
    group = request['preset']['groups'][0]
    group['comparisonPairs'] = [['metric', 'flat-child']]
    validator = Draft202012Validator(layout_contract.input_schema)
    assert not list(validator.iter_errors(request))
    for pairs in [[['metric']], [['metric', 'flat-child', 'third']], [['metric', 'metric']], 'metric']:
        group['comparisonPairs'] = pairs
        assert list(validator.iter_errors(request))


def test_grouped_reorder_requires_explicit_confirmation_and_complete_shape():
    root = Path(__file__).resolve().parents[1]
    contract = load_contract_registry(root / 'contracts/davinci-agent-v2.json').get('dashboard.set_widget_layout')
    validator = Draft202012Validator(contract.input_schema)
    preset = {'mode': 'reorder', 'sizing': 'content', 'orderedWidgetIds': ['a', 'b'],
              'groups': [{'title': '整体汇总', 'widgetIds': ['a', 'b']}],
              'groupingConfirmed': True}
    assert not list(validator.iter_errors({'preset': preset, 'expectedResourceRevision': 8}))
    assert list(validator.iter_errors({'preset': preset}))
    for invalid in [dict(preset, groupingConfirmed=False),
                    {key: value for key, value in preset.items() if key != 'groupingConfirmed'},
                    dict(preset, groups=[]), dict(preset, mode='compact')]:
        assert list(validator.iter_errors({'preset': invalid, 'expectedResourceRevision': 8}))
    assert not list(validator.iter_errors({'preset': {'mode': 'reorder', 'orderedWidgetIds': ['a', 'b']}}))


def test_reorder_accepts_bounded_complete_scope_orders(layout_contract):
    validator = Draft202012Validator(layout_contract.input_schema)
    request = {'preset': {'mode': 'reorder', 'orderedWidgetIds': ['root', 'flat'],
                          'scopeOrders': [
                              {'layoutWidgetId': 'flat', 'orderedWidgetIds': ['b', 'a']},
                              {'layoutWidgetId': 'tab', 'orderedWidgetIds': ['page-2', 'page-1']},
                          ]}}
    assert not list(validator.iter_errors(request))
    for invalid in [
        [{'layoutWidgetId': 'flat', 'orderedWidgetIds': []}],
        [{'layoutWidgetId': 'flat', 'orderedWidgetIds': ['a', 'a']}],
        [{'layoutWidgetId': '', 'orderedWidgetIds': ['a']}],
        [{'layoutWidgetId': 'flat', 'orderedWidgetIds': ['a'], 'extra': True}],
        [{'layoutWidgetId': str(i), 'orderedWidgetIds': ['a']} for i in range(201)],
    ]:
        changed = deepcopy(request)
        changed['preset']['scopeOrders'] = invalid
        assert list(validator.iter_errors(changed))


def test_new_flat_group_requires_at_least_two_cards(layout_contract):
    validator = Draft202012Validator(layout_contract.input_schema)
    request = {'expectedResourceRevision': 8, 'preset': {
        'mode': 'reorder', 'sizing': 'content',
        'orderedWidgetIds': ['a', 'b'], 'groupingConfirmed': True,
        'groups': [{'title': 'Summary', 'widgetIds': ['a']}],
    }}
    assert list(validator.iter_errors(request))



def test_option_three_grouping_and_outer_frame_rules_are_consistent(layout_contract):
    preset = layout_contract.input_schema['properties']['preset']['properties']
    descriptions = ' '.join([
        preset['groups']['description'],
        preset['groupingConfirmed']['description'],
        layout_contract.description,
    ])
    assert 'Choosing 3A/3B/3C authorizes full semantic regrouping' in descriptions
    assert 'second grouping/regroup question' in descriptions
    assert 'one group occupies 24 columns' in descriptions
    assert 'two consecutive lightweight narrow-card groups may occupy 12 columns each' in descriptions
    assert 'Three or more groups never share one row' in descriptions
    assert 'compact data-configuration profile and actual card type' in descriptions
    assert 'Never use a business-dimension whitelist' in descriptions
    assert 'numeric card limit' in descriptions


def test_group_count_has_no_four_group_ceiling(layout_contract):
    preset = layout_contract.input_schema['properties']['preset']['properties']
    assert 'maxItems' not in preset['groups']
    grouping = layout_contract.output_schema['properties']['data']['properties']['grouping']
    assert 'maxItems' not in grouping['properties']['groups']
    assert '1-4 native flat containers' not in layout_contract.description


def test_structure_receipt_exposes_bounded_configuration_relationship_profile():
    root = Path(__file__).resolve().parents[1]
    contract = load_contract_registry(root / 'contracts/davinci-agent-v2.json').get(
        'dashboard.get_structure'
    )
    widget = contract.output_schema['properties']['widgets']['items']
    profile = widget['properties']['semanticProfile']

    assert 'actual data configuration' in profile['description']
    assert 'no business-dimension whitelist' in profile['description']
    assert profile['additionalProperties'] is False
    assert set(profile['required']) == {
        'chartType', 'datasets', 'dimensions', 'metrics', 'filters',
        'grouping', 'time', 'drillPaths',
    }
    assert profile['properties']['dimensions']['maxItems'] == 16
    assert profile['properties']['filters']['maxItems'] == 12
    assert profile['properties']['drillPaths']['maxItems'] == 8


@pytest.mark.parametrize('dry_run', [False, True])
def test_regroup_accepts_effective_roots_and_explicit_approval(layout_contract, dry_run):
    request = regroup_request()
    request['dryRun'] = dry_run
    Draft202012Validator(layout_contract.input_schema).validate(request)


@pytest.mark.parametrize('sizing', ['preserve', 'auto'])
def test_grouping_rejects_non_compact_sizing(layout_contract, sizing):
    request = regroup_request()
    request['preset']['sizing'] = sizing
    assert list(Draft202012Validator(layout_contract.input_schema).iter_errors(request))


@pytest.mark.parametrize('path', [
    ('expectedResourceRevision',),
    ('preset', 'groups'),
    ('preset', 'groupingConfirmed'),
    ('preset', 'mode'),
    ('preset', 'orderedWidgetIds'),
    ('preset', 'regroup', 'confirmed'),
    ('preset', 'regroup', 'containerWidgetIds'),
])
def test_regroup_rejects_missing_prerequisites_even_for_preview(layout_contract, path):
    request = regroup_request()
    request['dryRun'] = True
    target = request
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    assert list(Draft202012Validator(layout_contract.input_schema).iter_errors(request))


@pytest.mark.parametrize('invalid', [
    {'containerWidgetIds': [], 'confirmed': True},
    {'containerWidgetIds': ['old-tab', 'old-tab'], 'confirmed': True},
    {'containerWidgetIds': [str(i) for i in range(201)], 'confirmed': True},
    {'containerWidgetIds': [''], 'confirmed': True},
    {'containerWidgetIds': ['x' * 101], 'confirmed': True},
    {'containerWidgetIds': [1], 'confirmed': True},
    {'containerWidgetIds': 'old-tab', 'confirmed': True},
    {'containerWidgetIds': ['old-tab'], 'confirmed': False},
    {'containerWidgetIds': ['old-tab'], 'confirmed': 'true'},
    {'containerWidgetIds': ['old-tab'], 'confirmed': True, 'deleteChildren': True},
    None,
])
def test_regroup_rejects_invalid_removal_list_or_confirmation(layout_contract, invalid):
    request = regroup_request()
    request['preset']['regroup'] = invalid
    assert list(Draft202012Validator(layout_contract.input_schema).iter_errors(request))


@pytest.mark.parametrize('patch', [
    {'mode': 'compact'}, {'mode': 'align'}, {'groups': []},
    {'groupingConfirmed': False}, {'orderedWidgetIds': []},
    {'orderedWidgetIds': ['metric', 'metric']},
])
def test_regroup_rejects_incompatible_presets(layout_contract, patch):
    request = regroup_request()
    request['preset'].update(patch)
    assert list(Draft202012Validator(layout_contract.input_schema).iter_errors(request))


def test_regroup_allows_removal_list_boundary(layout_contract):
    request = regroup_request()
    request['preset']['regroup']['containerWidgetIds'] = [
        str(i).zfill(100) for i in range(200)
    ]
    Draft202012Validator(layout_contract.input_schema).validate(request)


def test_no_regroup_keeps_existing_input_paths_valid(layout_contract):
    validator = Draft202012Validator(layout_contract.input_schema)
    for request in [
        {'preset': {'mode': 'compact', 'sizing': 'content'}},
        {'preset': {'mode': 'align', 'sizing': 'preserve'}},
        {'preset': {'mode': 'reorder', 'orderedWidgetIds': ['metric', 'old-tab']}},
        {'preset': {'mode': 'reorder', 'sizing': 'content', 'orderedWidgetIds': ['metric', 'other'],
                    'groups': [{'title': 'Summary', 'widgetIds': ['metric', 'other']}],
                    'groupingConfirmed': True}, 'expectedResourceRevision': 8},
        {'items': [{'widgetId': 'metric', 'x': 0}]},
    ]:
        validator.validate(request)


def test_regroup_schema_allows_complete_nested_removal_and_unaffected_roots(layout_contract):
    """Schema shape only: the planner owns hierarchy and complete-order checks.

    For outer -> direct-child + inner -> hidden-child, both containers must
    be selected. An unrelated Tab stays a root with its children protected.
    A complete effective order need not put every root in a new group.
    """
    request = {
        'expectedResourceRevision': 8,
        'preset': {
            'mode': 'reorder',
            'sizing': 'content',
            'orderedWidgetIds': ['metric', 'direct-child', 'hidden-child', 'unaffected-tab'],
            'groups': [{'title': 'Summary', 'widgetIds': ['metric', 'direct-child']}],
            'groupingConfirmed': True,
            'regroup': {'containerWidgetIds': ['outer', 'inner'], 'confirmed': True},
        },
    }
    Draft202012Validator(layout_contract.input_schema).validate(request)


def test_regroup_contract_documents_all_flat_scope_and_tab_preservation(layout_contract):
    preset = layout_contract.input_schema['properties']['preset']['properties']
    scope = preset['regroup']['properties']['containerWidgetIds']['description']
    assert 'Every existing native flat container Widget ID' in scope
    assert 'Tab containers are excluded from removal' in scope
    assert 'does not debate or request confirmation for individual IDs' in scope
    assert 'Every released flat child ID, data and non-layout config stays unchanged' \
        in preset['regroup']['description']


def test_grouping_contract_distinguishes_preview_from_save_authorization(layout_contract):
    properties = layout_contract.input_schema['properties']
    preset = properties['preset']['properties']
    assert 'Choosing 3A/3B/3C authorizes full semantic regrouping' in preset['groupingConfirmed']['description']
    assert 'without a second regroup or exact-proposal confirmation' in preset['groupingConfirmed']['description']
    confirmed = preset['regroup']['properties']['confirmed']['description']
    assert 'No second exact-proposal confirmation is required' in confirmed
    assert 'dryRun preview still does not authorize a later save' in confirmed
    assert 'exact shown proposal' in properties['dryRun']['description']
    assert 'dryRun:true only' in properties['dryRun']['description']
    assert 'explicit save approval' in properties['dryRun']['description']
    assert 'Declined grouping uses ordinary reorder;' not in layout_contract.description
    assert 'Do not ask whether to create groups, whether to preserve old flat groups,' \
        in layout_contract.description
    assert 'second regroup or exact-proposal confirmation' in layout_contract.description


def test_option_three_authorizes_rebuilding_existing_flat_groups(layout_contract):
    preset = layout_contract.input_schema['properties']['preset']['properties']
    descriptions = ' '.join([
        preset['groups']['description'],
        preset['groupingConfirmed']['description'],
        preset['regroup']['description'],
        preset['regroup']['properties']['containerWidgetIds']['description'],
        layout_contract.description,
    ])
    assert 'Choosing 3A/3B/3C authorizes full semantic regrouping' in descriptions
    assert 'Every existing native flat container Widget ID' in descriptions
    assert 'released flat children and current roots' in descriptions
    assert 'Tab containers are excluded from removal' in descriptions
    assert 'Do not ask for a second regroup confirmation' in descriptions
    assert 'First ask preserve existing vs fully regroup' not in descriptions
    assert 'When existing containers are found, first ask preserve existing vs fully regroup' not in descriptions
    assert preset['groups']['items']['properties']['widgetIds']['minItems'] == 2
    grouped_rule = layout_contract.input_schema['properties']['preset']['allOf'][1]['then']
    assert 'sizing' in grouped_rule['required']
    assert grouped_rule['properties']['sizing']['const'] == 'content'


@pytest.mark.parametrize('dry_run', [False, True])
def test_grouping_receipt_accepts_optional_approved_removals(layout_contract, dry_run):
    receipt = {
        'status': 'success', 'issues': [],
        'observed': {'observedAt': '2026-09-11T00:00:00Z'},
        'data': {
            'resourceId': 'dashboard', 'resourceRevision': 8 if dry_run else 9,
            'persisted': not dry_run, 'preview': dry_run, 'layoutChanges': [],
            'grouping': {
                'groups': [{'containerWidgetId': 'new-flat', 'title': 'Summary',
                            'widgetIds': ['metric', 'flat-child', 'hidden-tab-child']}],
                'removedContainerWidgetIds': ['old-flat', 'old-tab'],
            },
        },
    }
    validator = Draft202012Validator(layout_contract.output_schema)
    validator.validate(receipt)
    del receipt['data']['grouping']['removedContainerWidgetIds']
    validator.validate(receipt)
    for invalid in [['old-tab', 'old-tab'], [''], ['x' * 101], [1],
                    [str(i) for i in range(201)]]:
        receipt['data']['grouping']['removedContainerWidgetIds'] = invalid
        assert list(validator.iter_errors(receipt))


@pytest.mark.asyncio
async def test_regroup_keeps_conditional_only_model_projection(layout_contract):
    """Exercise the existing projection without changing its canonical validator."""
    from mcp import types

    from app.agui.claude_tools import (
        _without_schema_annotations,
        build_deferred_davinci_mcp_server,
        plan_native_tools,
    )
    from app.runtime.contracts import RuntimeFrontendTool

    canonical = deepcopy(dict(layout_contract.input_schema))
    assert all('if' in branch and 'then' in branch and
               set(branch) <= {'if', 'then', 'else'} for branch in canonical['allOf'])
    tool = RuntimeFrontendTool(name=layout_contract.action,
                               description=layout_contract.description,
                               parameters=deepcopy(canonical))
    server = build_deferred_davinci_mcp_server(plan_native_tools([tool]))
    handler = server['instance'].request_handlers[types.ListToolsRequest]
    result = await handler(types.ListToolsRequest(method='tools/list'))
    advertised = result.root.tools[0]
    assert not {'oneOf', 'anyOf', 'allOf'} & advertised.inputSchema.keys()
    assert advertised.inputSchema['properties'] == _without_schema_annotations(
        canonical['properties']
    )
    assert 'regroup' in advertised.inputSchema['properties']['preset']['properties']
    assert 'expectedResourceRevision is mandatory' in advertised.description
    assert tool.parameters == canonical
    validator = Draft202012Validator(tool.parameters)
    validator.validate(regroup_request())
    missing_revision = regroup_request()
    del missing_revision['expectedResourceRevision']
    assert list(validator.iter_errors(missing_revision))
