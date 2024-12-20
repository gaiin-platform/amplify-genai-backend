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
    "/google/integrations/sheets/get-rows": {
        "get_rows": lambda for_user, with_data: True
    },
    "/google/integrations/sheets/get-info": {
        "get_google_sheets_info": lambda for_user, with_data: True
    },
    "/google/integrations/sheets/get-sheet-names": {
        "get_sheet_names": lambda for_user, with_data: True
    },
    "/google/integrations/sheets/insert-rows": {
        "insert_rows": lambda for_user, with_data: True
    },
    "/google/integrations/sheets/delete-rows": {
        "delete_rows": lambda for_user, with_data: True
    },
    "/google/integrations/sheets/update-rows": {
        "update_rows": lambda for_user, with_data: True
    },
    "/google/integrations/sheets/create-spreadsheet": {
        "create_spreadsheet": lambda for_user, with_data: True
    },
    "/google/integrations/sheets/duplicate-sheet": {
        "duplicate_sheet": lambda for_user, with_data: True
    },
    "/google/integrations/sheets/rename-sheet": {
        "rename_sheet": lambda for_user, with_data: True
    },
    "/google/integrations/sheets/clear-range": {
        "clear_range": lambda for_user, with_data: True
    },
    "/google/integrations/sheets/apply-formatting": {
        "apply_formatting": lambda for_user, with_data: True
    },
    "/google/integrations/sheets/add-chart": {
        "add_chart": lambda for_user, with_data: True
    },
    "/google/integrations/sheets/get-cell-formulas": {
        "get_cell_formulas": lambda for_user, with_data: True
    },
    "/google/integrations/sheets/find-replace": {
        "find_replace": lambda for_user, with_data: True
    },
    "/google/integrations/sheets/sort-range": {
        "sort_range": lambda for_user, with_data: True
    },
    "/google/integrations/sheets/apply-conditional-formatting": {
        "apply_conditional_formatting": lambda for_user, with_data: True
    },
    "/google/integrations/sheets/execute-query": {
        "execute_query": lambda for_user, with_data: True
    },
    "/google/integrations/docs/create-document": {
        "create_new_document": lambda for_user, with_data: True
    },
    "/google/integrations/docs/get-contents": {
        "get_document_contents": lambda for_user, with_data: True
    },
    "/google/integrations/docs/insert-text": {
        "insert_text": lambda for_user, with_data: True
    },
    "/google/integrations/docs/replace-text": {
        "replace_text": lambda for_user, with_data: True
    },
    "/google/integrations/docs/create-outline": {
        "create_document_outline": lambda for_user, with_data: True
    },
    "/google/integrations/docs/export-document": {
        "export_document": lambda for_user, with_data: True
    },
    "/google/integrations/docs/share-document": {
        "share_document": lambda for_user, with_data: True
    },
    "/google/integrations/docs/find-text-indices": {
        "find_text_indices": lambda for_user, with_data: True
    },
    "/google/integrations/docs/append-text": {
        "append_text": lambda for_user, with_data: True
    },
    "/google/integrations/calendar/create-event": {
        "create_event": lambda for_user, with_data: True
    },
    "/google/integrations/calendar/update-event": {
        "update_event": lambda for_user, with_data: True
    },
    "/google/integrations/calendar/delete-event": {
        "delete_event": lambda for_user, with_data: True
    },
    "/google/integrations/calendar/get-event-details": {
        "get_event_details": lambda for_user, with_data: True
    },
    "/google/integrations/calendar/get-events-between-dates": {
        "get_events_between_dates": lambda for_user, with_data: True
    },
    "/google/integrations/calendar/get-events-for-date": {
        "get_events_for_date": lambda for_user, with_data: True
    },
    "/google/integrations/calendar/get-upcoming-events": {
        "get_upcoming_events": lambda for_user, with_data: True
    },
    "/google/integrations/calendar/get-free-time-slots": {
        "get_free_time_slots": lambda for_user, with_data: True
    },
    "/google/integrations/calendar/check-event-conflicts": {
        "check_event_conflicts": lambda for_user, with_data: True
    },
    "/google/integrations/drive/list-files": {
        "list_files": lambda for_user, with_data: True
    },
    "/google/integrations/drive/search-files": {
        "search_files": lambda for_user, with_data: True
    },
    "/google/integrations/drive/get-file-metadata": {
        "get_file_metadata": lambda for_user, with_data: True
    },
    "/google/integrations/drive/get-file-content": {
        "get_file_content": lambda for_user, with_data: True
    },
    "/google/integrations/drive/create-file": {
        "create_file": lambda for_user, with_data: True
    },
    "/google/integrations/drive/get-download-link": {
        "get_download_link": lambda for_user, with_data: True
    },
    "/google/integrations/drive/create-shared-link": {
        "create_shared_link": lambda for_user, with_data: True
    },
    "/google/integrations/drive/share-file": {
        "share_file": lambda for_user, with_data: True
    },
    "/google/integrations/drive/convert-file": {
        "convert_file": lambda for_user, with_data: True
    },
    "/google/integrations/drive/list-folders": {
        "list_folders": lambda for_user, with_data: True
    },
    "/google/integrations/drive/move-item": {
        "move_item": lambda for_user, with_data: True
    },
    "/google/integrations/drive/copy-item": {
        "copy_item": lambda for_user, with_data: True
    },
    "/google/integrations/drive/rename-item": {
        "rename_item": lambda for_user, with_data: True
    },
    "/google/integrations/drive/get-file-revisions": {
        "get_file_revisions": lambda for_user, with_data: True
    },
    "/google/integrations/drive/create-folder": {
        "create_folder": lambda for_user, with_data: True
    },
    "/google/integrations/drive/delete-item-permanently": {
        "delete_item_permanently": lambda for_user, with_data: True
    },
    "/google/integrations/drive/get-root-folder-ids": {
        "get_root_folder_ids": lambda for_user, with_data: True
    },
    "/google/integrations/forms/create-form": {
        "create_form": lambda for_user, with_data: True
    },

    "/google/integrations/forms/get-form-details": {
        "get_form_details": lambda for_user, with_data: True
    },

    "/google/integrations/forms/add-question": {
        "add_question": lambda for_user, with_data: True
    },

    "/google/integrations/forms/update-question": {
        "update_question": lambda for_user, with_data: True
    },

    "/google/integrations/forms/delete-question": {
        "delete_question": lambda for_user, with_data: True
    },

    "/google/integrations/forms/get-responses": {
        "get_responses": lambda for_user, with_data: True
    },

    "/google/integrations/forms/get-response": {
        "get_response": lambda for_user, with_data: True
    },

    "/google/integrations/forms/set-form-settings": {
        "set_form_settings": lambda for_user, with_data: True
    },

    "/google/integrations/forms/get-form-link": {
        "get_form_link": lambda for_user, with_data: True
    },

    "/google/integrations/forms/update-form-info": {
        "update_form_info": lambda for_user, with_data: True
    },

    "/google/integrations/forms/list-user-forms": {
        "list_user_forms": lambda for_user, with_data: True
    }
}
