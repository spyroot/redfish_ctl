import logging
from unittest import TestCase
from unittest.mock import Mock
from requests.models import Response
from redfish_ctl.redfish_manager import RedfishManager
from tests.test_utils import create_json_resp

the_response = Mock(spec=Response)

logging.basicConfig()
log = logging.getLogger("LOG")


class TestRedfishRespondMsg(TestCase):
    redfish_api = None

    def test_base_respond(self):
        data = {
            '@Message.ExtendedInfo':
                [
                    {
                        'Message': 'The request completed successfully.',
                        'MessageArgs': [], 'MessageArgs@odata.count': 0,
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
        self.assertTrue(
            redfish_resp.status_code == 200,
            f"status code must be 200"
        )
        err_string = ""
        extended_info = data['@Message.ExtendedInfo']
        self.assertTrue(
            len(extended_info) == len(redfish_resp.message_extended),
            f"expected len {len(data['@Message.ExtendedInfo'])} "
            f"got len {len(redfish_resp.message_extended)}"
        )
        self.assertTrue(redfish_resp.message_extended[0].message == extended_info[0]["Message"])
        self.assertTrue(redfish_resp.message_extended[0].message_id == extended_info[0]["MessageId"])
        self.assertTrue(redfish_resp.message_extended[0].severity == extended_info[0]["Severity"])

    def test_base_respond_with_pd(self):
        data = {
            '@Message.ExtendedInfo':
                [
                    {
                        'Message': 'The request completed successfully.',
                        'MessageArgs': [], 'MessageArgs@odata.count': 0,
                        'MessageId': 'Base.1.12.Success',
                        'RelatedProperties': [],
                        'RelatedProperties@odata.count': 0,
                        'Resolution': 'None',
                        'Severity': 'OK'
                    }
                ],
            'DriversAttachStatus': 'NotAttached',
            'ISOAttachStatus': 'NotAttached'
        }

        resp = create_json_resp(data)
        redfish_resp = RedfishManager.parse_json_respond_msg(resp)
        self.assertTrue(
            redfish_resp.status_code == 200,
            f"status code must be 200"
        )
