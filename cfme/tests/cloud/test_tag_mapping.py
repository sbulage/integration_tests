import fauxfactory
import pytest
from widgetastic.utils import partial_match
from wrapanapi.exceptions import ImageNotFoundError

from cfme import test_requirements
from cfme.cloud.provider.azure import AzureProvider
from cfme.cloud.provider.ec2 import EC2Provider
from cfme.exceptions import ItemNotFound
from cfme.markers.env_markers.provider import ONE_PER_TYPE
from cfme.utils.appliance.implementations.ui import navigate_to
from cfme.utils.log import logger
from cfme.utils.wait import wait_for

pytestmark = [
    pytest.mark.provider([EC2Provider], scope='function'),
    pytest.mark.usefixtures('setup_provider', 'refresh_provider'),
    test_requirements.tag
]


@pytest.fixture(scope='function')
def map_tags(appliance, provider, request):
    tag = appliance.collections.map_tags.create(entity_type=partial_match(provider.name.title()),
                                                label='test',
                                                category='Testing')
    yield tag
    request.addfinalizer(lambda: tag.delete())


@pytest.fixture(scope='function')
def tagged_vm(provider):
    # cu-24x7 vm is tagged with test:testing in provider
    tag_vm = provider.data.cap_and_util.capandu_vm
    collection = provider.appliance.provider_based_collection(provider)
    try:
        return collection.instantiate(tag_vm, provider)
    except IndexError:
        raise ItemNotFound('VM for tag mapping not found!')


@pytest.fixture(scope='function')
def refresh_provider(provider):
    provider.refresh_provider_relationships(wait=600)
    return True


@pytest.fixture(params=['instances', 'images'])
def tag_mapping_items(request, appliance, provider):
    entity_type = request.param
    collection = getattr(appliance.collections, 'cloud_{}'.format(entity_type))
    collection.filters = {'provider': provider}
    view = navigate_to(collection, 'AllForProvider')
    name = view.entities.get_first_entity().name
    try:
        mgmt_item = (
            provider.mgmt.get_template(name)
            if entity_type == 'images'
            else provider.mgmt.get_vm(name)
        )
    except ImageNotFoundError:
        msg = 'Failed looking up template [{}] from CFME on provider: {}'.format(name, provider)
        logger.exception(msg)
        pytest.skip(msg)
    return collection.instantiate(name=name, provider=provider), mgmt_item, entity_type


def tag_components():
    # Return tuple with random tag_label and tag_value
    return (
        fauxfactory.gen_alphanumeric(15, start="tag_label_"),
        fauxfactory.gen_alphanumeric(15, start="tag_value_")
    )


@pytest.mark.provider([AzureProvider], selector=ONE_PER_TYPE, scope='function')
def test_tag_mapping_azure_instances(tagged_vm, map_tags):
    """"
    Polarion:
        assignee: anikifor
        casecomponent: Cloud
        caseimportance: high
        initialEstimate: 1/12h
        testSteps:
            1. Find Instance that tagged with test:testing in Azure (cu-24x7)
            2. Create tag mapping for Azure instances
            3. Refresh Provider
            4. Go to Summary of the Instance and read Smart Management field
        expectedResults:
            1.
            2.
            3.
            4. Field value is "My Company Tags Testing: testing"
    """
    tagged_vm.provider.refresh_provider_relationships()
    view = navigate_to(tagged_vm, 'Details')

    def my_company_tags():
        return view.tag.get_text_of('My Company Tags') != 'No My Company Tags have been assigned'
    # sometimes it's not updated immediately after provider refresh
    wait_for(
        my_company_tags,
        timeout=600,
        delay=45,
        fail_func=view.toolbar.reload.click
    )
    assert view.tag.get_text_of('My Company Tags')[0] == 'Testing: testing'


# TODO: Azure needs tagging support in wrapanapi
def test_labels_update(provider, tag_mapping_items, soft_assert):
    """" Test updates of tag labels on entity details

    Polarion:
        assignee: anikifor
        casecomponent: Cloud
        caseimportance: high
        initialEstimate: 1/12h
        testSteps:
            1. Set a tag through provider mgmt interface
            2. Refresh Provider
            3. Go to entity details and get labels
            4. unset tag through provider mgmt interface
            5. Go to entity details and get labels
        expectedResults:
            1.
            2.
            3. labels includes label + tag
            4.
            5. labels should not include tag label
    """
    entity, mgmt_entity, entity_type = tag_mapping_items
    tag_label, tag_value = tag_components()
    mgmt_entity.set_tag(tag_label, tag_value)
    provider.refresh_provider_relationships(method='ui')
    view = navigate_to(entity, 'Details')
    # get_tags() doesn't work here as we're looking at labels, not smart management
    current_tag_value = view.entities.summary('Labels').get_text_of(tag_label)
    soft_assert(
        current_tag_value == tag_value, (
            'Tag values is not that expected, actual - {}, expected - {}'.format(
                current_tag_value, tag_value
            )
        )
    )
    mgmt_entity.unset_tag(tag_label, tag_value)
    provider.refresh_provider_relationships(method='ui')
    view = navigate_to(entity, 'Details', force=True)
    fields = view.entities.summary('Labels').fields
    soft_assert(
        tag_label not in fields,
        '{} label was not removed from details page'.format(tag_label)
    )


# TODO: Azure needs tagging support in wrapanapi
def test_mapping_tags(
    appliance, provider, tag_mapping_items, soft_assert, category, request
):
    """Test mapping tags on provider instances and images
    Polarion:
        assignee: anikifor
        casecomponent: Cloud
        caseimportance: high
        initialEstimate: 1/12h
        testSteps:
            1. Set a tag through provider mgmt interface
            2. create a CFME tag map for entity type
            3. Go to entity details and get smart management table
            4. Delete the tag map
            5. Go to entity details and get smart management table
        expectedResults:
            1.
            2.
            3. smart management should include category name and tag
            4.
            5. smart management table should NOT include category name and tag
    """
    entity, mgmt_entity, entity_type = tag_mapping_items
    tag_label, tag_value = tag_components()
    mgmt_entity.set_tag(tag_label, tag_value)
    request.addfinalizer(
        lambda: mgmt_entity.unset_tag(tag_label, tag_value)
    )

    provider_type = provider.discover_name.split(' ')[0]
    # Check the add form to find the correct resource entity type selection string
    view = navigate_to(appliance.collections.map_tags, 'Add')
    select_text = None  # init this since we set it within if, and reference it in for/else:
    options = []  # track the option strings for logging in failure
    for option in view.resource_entity.all_options:
        option_text = option.text  # read it once since its used multiple times
        options.append(option_text)
        if provider_type in option_text and entity_type.capitalize()[:-1] in option_text:
            select_text = option_text
            break
    else:
        # no match / break for select_text
        if select_text is None:
            pytest.fail(
                'Failed to match the entity type [{e}] and provider type [{p}] in options: [{o}]'
                .format(e=entity_type, p=provider_type, o=options)
            )
    view.cancel_button.click()  # close the open form

    map_tag = appliance.collections.map_tags.create(
        entity_type=select_text,
        label=tag_label,
        category=category.name
    )

    # check the tag shows up
    provider.refresh_provider_relationships(method='ui')
    soft_assert(any(
        tag.category.display_name == category.name and tag.display_name == tag_value
        for tag in entity.get_tags()
    ), '{}: {} was not found in tags'.format(category.name, tag_value))

    # delete it
    map_tag.delete()

    # check the tag goes away
    provider.refresh_provider_relationships(method='ui')
    soft_assert(not '{}: {}'.format(category.name, tag_value) in entity.get_tags())


@pytest.mark.tier(2)
@pytest.mark.parametrize("collection_type", ["vms", "templates"])
@pytest.mark.provider([EC2Provider], scope='function')
def test_ec2_tags(provider, request, collection_type, testing_instance):
    """
    Requirement: Have an ec2 provider

    Polarion:
        assignee: anikifor
        casecomponent: Cloud
        caseimportance: medium
        initialEstimate: 1/6h
        startsin: 5.8
        testSteps:
            1. Create an instance/choose image
            2. tag it with test:testing on EC side
            3. Refresh provider
            4. Go to summary of this instance/image and check whether there is
            test:testing in Labels field
            5. Delete that instance/untag image
    """
    tag_key = f"test_{fauxfactory.gen_alpha()}"
    tag_value = f"testing_{fauxfactory.gen_alpha()}"
    if collection_type == "templates":
        taggable = provider.mgmt.list_templates()[0]
        request.addfinalizer(lambda: taggable.unset_tag(tag_key, tag_value))
    else:
        taggable = testing_instance.mgmt
    taggable.set_tag(tag_key, tag_value)
    provider.refresh_provider_relationships(wait=600)
    collection = provider.appliance.provider_based_collection(provider, coll_type=collection_type)
    taggable_in_cfme = collection.instantiate(taggable.name, provider)
    view = navigate_to(taggable_in_cfme, 'Details')
    assert view.entities.summary("Labels").get_text_of(tag_key) == tag_value
