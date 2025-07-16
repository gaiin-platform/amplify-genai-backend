import re

INPUT_FILE = "table_test.md"
OUTPUT_FILE = "final_table.md"

def replace_placeholders(name):
    name = name.replace('${self:custom.stageVars.DEP_NAME}', 'v6')
    name = re.sub(r"\${opt:stage, ?['\"]dev['\"]}", 'dev', name)
    return name

def extract_resolved_tables(input_file, output_file):
    final_tables = []

    with open(input_file, 'r') as f:
        for line in f:
            if line.strip().startswith('|') and not line.startswith('| #'):
                parts = [p.strip() for p in line.strip().split('|')]
                if len(parts) >= 5:
                    raw_resolved = parts[4].strip('` ')
                    cleaned = replace_placeholders(raw_resolved)
                    final_tables.append(cleaned)

    # Deduplicate and sort (optional)
    unique_tables = sorted(set(final_tables))

    with open(output_file, 'w') as f:
        f.write("# Final Resolved DynamoDB Table Names\n\n")
        for i, name in enumerate(unique_tables, 1):
            f.write(f"| {i} | {name}\n")

    print(f"✅ Saved cleaned table names to: {output_file}")

if __name__ == "__main__":
    extract_resolved_tables(INPUT_FILE, OUTPUT_FILE)
