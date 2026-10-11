import json


def shape(value):
    return json.dumps(value, sort_keys=True)
