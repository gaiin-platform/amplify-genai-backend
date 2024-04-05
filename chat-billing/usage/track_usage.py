# determine what kind of usage was received from the table via itemType
# itemType could be tokens (chat), assistant thread (code interpreter), lambda, infrastructure, etc.
# expect to receive itemType and quantity

# this function should call the necessary functions/files depending on the itemType received
# if no item type is received, assume that it is a chat conversation
# ****************************

# first, receive event from the DynamoDB table: vu-amplify-billing
