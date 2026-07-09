# https://developer.dell.com/apis/2978/versions/6.xx/openapi.yaml/paths/~1redfish~1v1~1JobService~1Jobs/post

# def accepted(self):
#     """A resource of type Job has been created"""
#     return 201


# def accepted(self):
#     """Accepted; a Task has been generated"""
#     return 202
#
#
# def success(self):
#     """Success, but no response data
#     :return:
#     """
#     return 204

# /redfish/v1/Systems/{ComputerSystemId}/SecureBoot
# The SecureBoot schema contains UEFI Secure Boot information and represents properties for managing the UEFI Secure Boot functionality of a system.

# Patch
# https://developer.dell.com/apis/2978/versions/6.xx/openapi.yaml/paths/~1redfish~1v1~1Systems~1%7BComputerSystemId%7D~1SecureBoot/patch

# Post
#https://developer.dell.com/apis/2978/versions/6.xx/openapi.yaml/paths/~1redfish~1v1~1Systems~1%7BComputerSystemId%7D~1SecureBoot~1Actions~1SecureBoot.ResetKeys/post

# NOTE: ResetKeyType property also supports the following parameters:
#
# ResetPK
# ResetKEK
# ResetDB
# ResetDBX


# /redfish/v1/Managers/{ManagerId}/Oem/Dell/DellJobService/Actions/DellJobService.CreateRebootJob