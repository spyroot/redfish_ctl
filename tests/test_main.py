import json
from unittest.mock import Mock
from requests.models import Response

from redfish_ctl.redfish_manager import RedfishManager
from tests.test_utils import create_json_resp


def test_base_respond():
    data = {
        '@Message.ExtendedInfo':
            [
                {
                    'Message': 'The request completed successfully.',
                    'MessageArgs': [],
                    'MessageArgs@odata.count': 0,
                    'MessageId': 'Base.1.12.Success',
                    'RelatedProperties': [],
                    'RelatedProperties@odata.count': 0,
                    'Resolution': 'None',
                    'Severity': 'OK'
                }
            ]
    }

    resp = create_json_resp(data)
    redfish_resp = RedfishManager.parse_json_respond_msg(resp)
    for msg_e in redfish_resp.message_extended:
        print(msg_e.message)
        print(msg_e.message_args)
        print(msg_e.related)
        print(msg_e.severity)
        print(msg_e.resolution)


test_base_respond()
