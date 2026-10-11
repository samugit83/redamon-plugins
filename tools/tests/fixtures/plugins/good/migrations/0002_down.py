def migrate_row(row):
    return {k: v for k, v in row.items() if k != "migrated"}
