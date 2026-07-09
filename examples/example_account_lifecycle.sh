# Create, inspect, and delete a Redfish account — vendor-neutral (Dell/HPE/Supermicro).
# Every write is dry-run by default; only --confirm mutates. Delete also refuses to
# remove the account you logged in as, so a misfire can't lock you out.

# See what a create would do (dry-run — writes nothing, password masked):
redfish_ctl account-create --username test --password 'S0me-Str0ng-Pw' --role ReadOnly

# Actually create it (least-privilege ReadOnly by default):
redfish_ctl account-create --username test --password 'S0me-Str0ng-Pw' --role ReadOnly --confirm

# Confirm it exists, then change its role:
redfish_ctl accounts --usernames
redfish_ctl account-update --username test --role Operator --confirm

# Remove it when done (irreversible — requires --confirm):
redfish_ctl account-delete --username test --confirm

# Notes from real hardware:
#  - Some BMCs (HPE iLO 5) process create as an async task ("AcceptedTaskGenerated"),
#    so the account may appear a moment after create returns.
#  - iLO rejects an $expand on the Accounts collection, so account lookup fetches
#    each member individually — no flag needed, it just works.
