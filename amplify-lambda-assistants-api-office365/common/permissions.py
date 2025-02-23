def get_permission_checker(user, ptype, op, data):
    print(
        "Checking permissions for user: {} and type: {} and op: {}".format(
            user, ptype, op
        )
    )
    return permissions_by_state_type.get(ptype, {}).get(
        op, lambda for_user, with_data: False
    )


def can_execute_custom_auto(user, data): 
    return True


"""
Every service must define the permissions for each operation
here. The permissions are defined as a dictionary of
dictionaries where the top level key is the path to the
service and the second level key is the operation. The value
is a function that takes a user and data and returns if the
user can do the operation.
"""
permissions_by_state_type = {
    "/microsoft/integrations" : {
        "get" : lambda for_user, with_data: True,
    },
    "/microsoft/integrations/route" : {
        "route" : lambda for_user, with_data: True,
    },

    # # Drive permissions
    # "/microsoft/integrations/drive/list-items": {
    #     "list_drive_items": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/drive/upload-file": {
    #     "upload_file": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/drive/download-file": {
    #     "download_file": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/drive/delete-item": {
    #     "delete_item": lambda for_user, with_data: True
    # },

    # # Excel permissions
    # "/microsoft/integrations/excel/list-worksheets": {
    #     "list_worksheets": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/excel/list-tables": {
    #     "list_tables": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/excel/add-row-to-table": {
    #     "add_row_to_table": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/excel/read-range": {
    #     "read_range": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/excel/update-range": {
    #     "update_range": lambda for_user, with_data: True
    # },

    # # Outlook permissions
    # "/microsoft/integrations/outlook/list-messages": {
    #     "list_messages": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/outlook/get-message-details": {
    #     "get_message_details": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/outlook/send-mail": {
    #     "send_mail": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/outlook/delete-message": {
    #     "delete_message": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/outlook/get-attachments": {
    #     "get_attachments": lambda for_user, with_data: True
    # },

    # # Planner permissions
    # "/microsoft/integrations/planner/list-plans": {
    #     "list_plans_in_group": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/planner/list-buckets": {
    #     "list_buckets_in_plan": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/planner/list-tasks": {
    #     "list_tasks_in_plan": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/planner/create-task": {
    #     "create_task": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/planner/update-task": {
    #     "update_task": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/planner/delete-task": {
    #     "delete_task": lambda for_user, with_data: True
    # },

    # # SharePoint permissions
    # "/microsoft/integrations/sharepoint/list-sites": {
    #     "list_sites": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/sharepoint/get-site": {
    #     "get_site_by_path": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/sharepoint/list-site-lists": {
    #     "list_site_lists": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/sharepoint/get-list-items": {
    #     "get_list_items": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/sharepoint/create-list-item": {
    #     "create_list_item": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/sharepoint/update-list-item": {
    #     "update_list_item": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/sharepoint/delete-list-item": {
    #     "delete_list_item": lambda for_user, with_data: True
    # },

    # # Teams permissions
    # "/microsoft/integrations/teams/list-teams": {
    #     "list_teams": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/teams/list-channels": {
    #     "list_channels": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/teams/create-channel": {
    #     "create_channel": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/teams/send-channel-message": {
    #     "send_channel_message": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/teams/get-chat-messages": {
    #     "get_chat_messages": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/teams/schedule-meeting": {
    #     "schedule_meeting": lambda for_user, with_data: True
    # },

    # # User Groups permissions
    # "/microsoft/integrations/user-groups/list-users": {
    #     "list_users": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/user-groups/get-user-details": {
    #     "get_user_details": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/user-groups/list-groups": {
    #     "list_groups": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/user-groups/get-group-details": {
    #     "get_group_details": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/user-groups/create-group": {
    #     "create_group": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/user-groups/delete-group": {
    #     "delete_group": lambda for_user, with_data: True
    # },

    # # OneNote permissions
    # "/microsoft/integrations/onenote/list-notebooks": {
    #     "list_notebooks": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/onenote/list-sections": {
    #     "list_sections_in_notebook": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/onenote/list-pages": {
    #     "list_pages_in_section": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/onenote/create-page": {
    #     "create_page_in_section": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/onenote/get-page-content": {
    #     "get_page_content": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/onenote/create-page-with-attachments": {
    #     "create_page_with_image_and_attachment": lambda for_user, with_data: True
    # },

    # # Contacts permissions
    # "/microsoft/integrations/contacts/list-contacts": {
    #     "list_contacts": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/contacts/get-contact-details": {
    #     "get_contact_details": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/contacts/create-contact": {
    #     "create_contact": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/contacts/delete-contact": {
    #     "delete_contact": lambda for_user, with_data: True
    # },

    # # Calendar permissions
    # "/microsoft/integrations/calendar/create-event": {
    #     "create_event": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/calendar/update-event": {
    #     "update_event": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/calendar/delete-event": {
    #     "delete_event": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/calendar/get-event-details": {
    #     "get_event_details": lambda for_user, with_data: True
    # },
    # "/microsoft/integrations/calendar/get-events-between-dates": {
    #     "get_events_between_dates": lambda for_user, with_data: True
    # }
}
