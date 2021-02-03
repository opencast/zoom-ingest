class BadWebhookData(Exception):
    pass


class NoMp4Files(Exception):
    pass


def get_config_ignore(config, group, key, ignore_blank_values):
    if config and group in config and key in config[group]:
        value = config[group][key]
        if ignore_blank_values:
            return value
        elif value:
            return value.strip()
        else:
            raise ValueError(f"The {key} value under { group } is invalid")
    else:
        raise KeyError(f"The { key } value under { group } is missing")

def get_config(config, group, key):
    return get_config_ignore(config, group, key, False)
