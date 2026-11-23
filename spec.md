# Implement Python script

This script uses Allegro API to modify user's ENDED offers.
The purpose of this offer is to briefly activate and then immediately END offer to avoid its automatic deletion by the system.

## Requirement
The script should do the following:

1. Get the list of user's offers that have status ENDED.
2. Update each offer with status ACTIVE
3. Check that the previous update was applied and then immediately send the request to end the offer

# Implementation details
- User's client id and client secret are specified in the `creds.py`
- The API is specified in `swagger.yaml`