"""The one exception type a plugin raises and receives."""


class PluginError(Exception):
    """An error with a stable code and a message a person can read.

    Raised by a plugin action, the UI shows `message`. Raised by the SDK, `code` is the
    Gateway's (e.g. `permission_denied`, `plugin_llm_cap`) or one of the client's own
    (`gateway_timeout`, `gateway_unreachable`, `gateway_error`, `gateway_bad_response`,
    `gateway_response_too_large`, `invalid_value`, `not_in_job`). Any other exception an
    action raises reaches the user only as "the plugin failed"; its details go to the
    plugin's log.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)

    def __repr__(self) -> str:
        return f"PluginError({self.code!r}, {self.message!r})"
