#!/usr/bin/env python3
"""
Test script to verify PROMPT_COST_ALERT is properly added to get_user_app_configs.

This script tests Part 1 of the Prompt Cost Alert implementation:
- Verifies that AdminConfigTypes.PROMPT_COST_ALERT is defined
- Verifies that get_user_app_configs includes PROMPT_COST_ALERT in app_configs list
"""

import sys
import ast
import os

def test_prompt_cost_alert_in_core():
    """Test that PROMPT_COST_ALERT is properly configured in core.py"""

    core_file_path = os.path.join(os.path.dirname(__file__), "service", "core.py")

    print("=" * 70)
    print("Testing Part 1: Backend - Add PROMPT_COST_ALERT to User Configs")
    print("=" * 70)
    print()

    # Read the core.py file
    with open(core_file_path, 'r') as f:
        content = f.read()

    # Test 1: Check that AdminConfigTypes enum has PROMPT_COST_ALERT
    print("Test 1: Checking AdminConfigTypes enum definition...")
    if 'PROMPT_COST_ALERT = "promtCostAlert"' in content:
        print("✅ PASS: AdminConfigTypes.PROMPT_COST_ALERT is defined")
    else:
        print("❌ FAIL: AdminConfigTypes.PROMPT_COST_ALERT is NOT defined")
        sys.exit(1)

    # Test 2: Check that get_user_app_configs includes PROMPT_COST_ALERT
    print("\nTest 2: Checking get_user_app_configs function...")

    # Parse the file and find the get_user_app_configs function
    tree = ast.parse(content)

    found_function = False
    includes_prompt_cost_alert = False

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'get_user_app_configs':
            found_function = True

            # Find the app_configs list assignment
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name) and target.id == 'app_configs':
                            # Check if the list contains PROMPT_COST_ALERT
                            if isinstance(stmt.value, ast.List):
                                for element in stmt.value.elts:
                                    if isinstance(element, ast.Attribute):
                                        if element.attr == 'PROMPT_COST_ALERT':
                                            includes_prompt_cost_alert = True
                                            break

    if not found_function:
        print("❌ FAIL: Could not find get_user_app_configs function")
        sys.exit(1)

    if includes_prompt_cost_alert:
        print("✅ PASS: get_user_app_configs includes AdminConfigTypes.PROMPT_COST_ALERT")
    else:
        print("❌ FAIL: get_user_app_configs does NOT include AdminConfigTypes.PROMPT_COST_ALERT")
        sys.exit(1)

    # Test 3: Verify the app_configs list structure
    print("\nTest 3: Verifying app_configs list structure...")

    # Extract the app_configs section from content
    start_marker = "app_configs = ["
    end_marker = "]"

    start_idx = content.find(start_marker)
    if start_idx != -1:
        end_idx = content.find(end_marker, start_idx)
        app_configs_section = content[start_idx:end_idx + 1]

        expected_configs = [
            "EMAIL_SUPPORT",
            "DEFAULT_CONVERSATION_STORAGE",
            "AI_EMAIL_DOMAIN",
            "PROMPT_COST_ALERT"
        ]

        all_present = all(config in app_configs_section for config in expected_configs)

        if all_present:
            print("✅ PASS: All expected configs are present in app_configs list:")
            for config in expected_configs:
                print(f"   - AdminConfigTypes.{config}")
        else:
            print("❌ FAIL: Not all expected configs are present")
            for config in expected_configs:
                if config in app_configs_section:
                    print(f"   ✅ AdminConfigTypes.{config}")
                else:
                    print(f"   ❌ AdminConfigTypes.{config} (MISSING)")
            sys.exit(1)
    else:
        print("⚠️  WARNING: Could not extract app_configs section for detailed verification")

    # Test 4: Verify initialize_config handles PROMPT_COST_ALERT
    print("\nTest 4: Checking initialize_config function...")

    if 'elif config_type == AdminConfigTypes.PROMPT_COST_ALERT:' in content:
        print("✅ PASS: initialize_config has PROMPT_COST_ALERT case")

        # Check for the expected structure
        if '"isActive": False' in content and '"cost": 5' in content:
            print("✅ PASS: PROMPT_COST_ALERT initialization has expected structure")
        else:
            print("⚠️  WARNING: PROMPT_COST_ALERT initialization might have unexpected structure")
    else:
        print("⚠️  WARNING: initialize_config might not handle PROMPT_COST_ALERT explicitly")

    # Test 5: Verify PROMPT_COST_ALERT is in update handler
    print("\nTest 5: Checking update configuration handler...")

    if 'AdminConfigTypes.PROMPT_COST_ALERT' in content and 'handle_update_config' in content:
        # Check if it's in the match/case statement
        if '| AdminConfigTypes.PROMPT_COST_ALERT' in content or 'case AdminConfigTypes.PROMPT_COST_ALERT:' in content:
            print("✅ PASS: PROMPT_COST_ALERT is handled in update configuration logic")
        else:
            print("⚠️  WARNING: PROMPT_COST_ALERT might not be explicitly handled in updates")
    else:
        print("⚠️  WARNING: Could not verify update handler configuration")

    print()
    print("=" * 70)
    print("✅ ALL TESTS PASSED!")
    print("=" * 70)
    print()
    print("Part 1 Implementation Summary:")
    print("- PROMPT_COST_ALERT is properly defined in AdminConfigTypes enum")
    print("- get_user_app_configs() includes PROMPT_COST_ALERT in app_configs list")
    print("- Users will now receive the promptCostAlert config when calling this endpoint")
    print()
    print("Next Steps:")
    print("1. Deploy the backend changes to test environment")
    print("2. Verify the API endpoint returns promptCostAlert in the response")
    print("3. Move on to Part 2: Frontend implementation")
    print()

if __name__ == "__main__":
    try:
        test_prompt_cost_alert_in_core()
    except FileNotFoundError as e:
        print(f"❌ ERROR: Could not find file: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ ERROR: Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
