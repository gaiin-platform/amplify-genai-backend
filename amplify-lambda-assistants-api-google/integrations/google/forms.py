from googleapiclient.discovery import build
import json

from integrations.google.drive import get_drive_service
from integrations.oauth import get_user_credentials
from google.oauth2.credentials import Credentials

integration_name = "google_forms"


def get_forms_service(current_user, access_token):
    user_credentials = get_user_credentials(
        current_user, integration_name, access_token
    )
    credentials = Credentials.from_authorized_user_info(user_credentials)
    return build("forms", "v1", credentials=credentials)


def create_form(current_user, title, description="", access_token=None):
    service = get_forms_service(current_user, access_token)
    form = {"info": {"title": title, "description": description}}
    result = service.forms().create(body=form).execute()
    return json.dumps({"form_id": result["formId"], "title": result["info"]["title"]})


def get_form_details(current_user, form_id, access_token=None):
    service = get_forms_service(current_user, access_token)
    result = service.forms().get(formId=form_id).execute()
    return json.dumps(result)


def add_question(
    current_user,
    form_id,
    question_type,
    title,
    required=False,
    options=None,
    access_token=None,
):
    service = get_forms_service(current_user, access_token)
    question = {
        "createItem": {
            "item": {
                "title": title,
                "questionItem": {
                    "question": {
                        "required": required,
                        "choiceQuestion": (
                            {
                                "type": question_type,
                                "options": [
                                    {"value": option} for option in (options or [])
                                ],
                            }
                            if question_type in ["RADIO", "CHECKBOX", "DROP_DOWN"]
                            else {}
                        ),
                    }
                },
            },
            "location": {"index": 0},
        }
    }
    result = (
        service.forms()
        .batchUpdate(formId=form_id, body={"requests": [question]})
        .execute()
    )
    return json.dumps({"question_id": result["replies"][0]["createItem"]["itemId"]})


def update_question(
    current_user,
    form_id,
    question_id,
    title=None,
    required=None,
    options=None,
    access_token=None,
):
    service = get_forms_service(current_user, access_token)
    update = {
        "updateItem": {
            "item": {
                "itemId": question_id,
                "title": title,
                "questionItem": {
                    "question": {
                        "required": required,
                        "choiceQuestion": (
                            {
                                "options": [
                                    {"value": option} for option in (options or [])
                                ]
                            }
                            if options
                            else {}
                        ),
                    }
                },
            },
            "updateMask": "title,questionItem.question.required,questionItem.question.choiceQuestion.options",
        }
    }
    result = (
        service.forms()
        .batchUpdate(formId=form_id, body={"requests": [update]})
        .execute()
    )
    return json.dumps({"updated": True})


def delete_question(current_user, form_id, question_id, access_token=None):
    service = get_forms_service(current_user, access_token)
    delete = {"deleteItem": {"itemId": question_id}}
    result = (
        service.forms()
        .batchUpdate(formId=form_id, body={"requests": [delete]})
        .execute()
    )
    return json.dumps({"deleted": True})


def get_responses(current_user, form_id, access_token=None):
    service = get_forms_service(current_user, access_token)
    result = service.forms().responses().list(formId=form_id).execute()
    return json.dumps(result)


def get_response(current_user, form_id, response_id, access_token=None):
    service = get_forms_service(current_user, access_token)
    result = (
        service.forms()
        .responses()
        .get(formId=form_id, responseId=response_id)
        .execute()
    )
    return json.dumps(result)


def set_form_settings(current_user, form_id, settings, access_token=None):
    service = get_forms_service(current_user, access_token)
    update = {
        "updateSettings": {
            "settings": settings,
            "updateMask": ",".join(settings.keys()),
        }
    }
    result = (
        service.forms()
        .batchUpdate(formId=form_id, body={"requests": [update]})
        .execute()
    )
    return json.dumps({"updated": True})


def move_question(current_user, form_id, question_id, new_index, access_token=None):
    service = get_forms_service(current_user, access_token)
    move = {"moveItem": {"itemId": question_id, "newPosition": {"index": new_index}}}
    result = (
        service.forms().batchUpdate(formId=form_id, body={"requests": [move]}).execute()
    )
    return json.dumps({"moved": True})


def add_page_break(current_user, form_id, index, access_token=None):
    service = get_forms_service(current_user, access_token)
    page_break = {
        "createItem": {"item": {"pageBreakItem": {}}, "location": {"index": index}}
    }
    result = (
        service.forms()
        .batchUpdate(formId=form_id, body={"requests": [page_break]})
        .execute()
    )
    return json.dumps({"page_break_id": result["replies"][0]["createItem"]["itemId"]})


def set_form_published(current_user, form_id, published, access_token=None):
    service = get_forms_service(current_user, access_token)
    update = {
        "updateSettings": {
            "settings": {"isPublished": published},
            "updateMask": "isPublished",
        }
    }
    result = (
        service.forms()
        .batchUpdate(formId=form_id, body={"requests": [update]})
        .execute()
    )
    return json.dumps({"published": published})


def get_form_responses_summary(current_user, form_id, access_token=None):
    service = get_forms_service(current_user, access_token)
    result = service.forms().responses().summaries().list(formId=form_id).execute()
    return json.dumps(result)


def get_form_link(current_user, form_id, access_token=None):
    service = get_forms_service(current_user, access_token)
    form = service.forms().get(formId=form_id).execute()
    response_url = form.get("responderUri", "")
    return json.dumps({"form_link": response_url})


def update_form_info(
    current_user, form_id, title=None, description=None, access_token=None
):
    # Get the Google Forms service for the current user
    service = get_forms_service(current_user, access_token)

    # Prepare the update request structure
    update_info = {"info": {}, "updateMask": []}  # Keep this consistently a list

    # Add title to the update if provided
    if title is not None:
        update_info["info"]["title"] = title
        update_info["updateMask"].append("title")

    # Add description to the update if provided
    if description is not None:
        update_info["info"]["description"] = description
        update_info["updateMask"].append("description")

    # Create the final request body
    update_request = {
        "updateFormInfo": {
            "info": update_info["info"],
            "updateMask": ",".join(update_info["updateMask"]),  # Convert to string here
        }
    }

    # Execute the batch update call to the Forms API
    try:
        result = (
            service.forms()
            .batchUpdate(formId=form_id, body={"requests": [update_request]})
            .execute()
        )
        return json.dumps({"updated": True, "title": title, "description": description})
    except Exception as e:
        return json.dumps({"updated": False, "error": str(e)})


def list_user_forms(current_user, access_token=None):
    drive_service = get_drive_service(current_user, access_token)
    try:
        results = (
            drive_service.files()
            .list(
                q="mimeType='application/vnd.google-apps.form'",
                spaces="drive",
                fields="files(id, name)",
            )
            .execute()
        )
        forms = results.get("files", [])
        return json.dumps([{"id": form["id"], "name": form["name"]} for form in forms])
    except Exception as e:
        raise Exception(f"Error listing user forms: {str(e)}")
