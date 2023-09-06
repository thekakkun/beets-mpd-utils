class Subcommand:
    func: function

    def __init__(
        self, name: str, help: str = "", aliases: tuple[str, ...] = ()
    ) -> None: ...
