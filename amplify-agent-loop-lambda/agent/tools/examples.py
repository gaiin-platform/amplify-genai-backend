import csv


def format_examples(csv_path, first_prefix, second_prefix, skip_first_line=True):
    formatted = []
    with open(csv_path) as f:
        reader = csv.reader(f)
        if skip_first_line:
            next(reader, None)
        for row in reader:
            if len(row) >= 2:
                formatted.extend(
                    [f"{first_prefix} {row[0]}", f"{second_prefix} {row[1]}"]
                )
    return "\n".join(formatted)
