import argparse
import json
import boto3
from pycommon.api.secrets import store_secret_parameter


def store_integration_config_as_secret(stage, integration, client_config, scopes):
    secret_name = f"integrations/{integration}/{stage}"
    configuration = {
        "client_config": json.loads(client_config),
        "scopes": json.loads(scopes),
    }
    store_secret_parameter(secret_name, json.dumps(configuration), "/oauth")
    print(f"Integration configuration for {integration} stored as secret.")


def list_integrations(show_details):
    ssm = boto3.client("ssm")
    prefix = "/oauth/integrations/"
    response = ssm.get_parameters_by_path(
        Path=prefix, Recursive=True, WithDecryption=False
    )
    print("List of integrations:")
    for parameter in response["Parameters"]:
        integration = parameter["Name"].replace(prefix, "", 1).split("/")[0]
        print(integration)
        if show_details:
            stages = [
                param.split("/")[-1]
                for param in response["Parameters"]
                if param.startswith(f"{prefix}{integration}/")
            ]
            print(f"Stages configured: {', '.join(stages)}")


def list_integration_details(integration, show_details):
    ssm = boto3.client("ssm")
    prefix = f"/oauth/integrations/{integration}/"
    response = ssm.get_parameters_by_path(
        Path=prefix, Recursive=False, WithDecryption=False
    )

    print(f"Details for integration '{integration}':")
    for parameter in response["Parameters"]:
        stage = parameter["Name"].replace(prefix, "", 1)
        print(f"Stage: {stage}")
        if show_details:
            config = ssm.get_parameter(Name=parameter["Name"], WithDecryption=True)[
                "Parameter"
            ]["Value"]
            config_dict = json.loads(config)
            print(f"Client Configuration for Stage {stage}:")
            print(json.dumps(config_dict["client_config"], indent=4))
            print(f"Scopes for Stage {stage}:")
            print(json.dumps(config_dict["scopes"], indent=4))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="store_integration_config.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    store_parser = subparsers.add_parser("store")
    store_parser.add_argument(
        "--stage", help="The stage of the integration", required=True
    )
    store_parser.add_argument(
        "--integration", help="The integration name", required=True
    )
    store_parser.add_argument(
        "--client-config", help="The client configuration JSON", required=True
    )
    store_parser.add_argument("--scopes", help="The scopes JSON array", required=True)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument(
        "--details", action="store_true", help="Show details of each integration"
    )

    details_parser = subparsers.add_parser("details")
    details_parser.add_argument("integration", help="The integration name")
    details_parser.add_argument(
        "--details",
        action="store_true",
        help="Show details of each stage for the integration",
    )

    args = parser.parse_args()

    if args.command == "list":
        list_integrations(show_details=args.details)
    elif args.command == "store":
        store_integration_config_as_secret(
            args.stage, args.integration, args.client_config, args.scopes
        )
    elif args.command == "details":
        list_integration_details(args.integration, args.details)
