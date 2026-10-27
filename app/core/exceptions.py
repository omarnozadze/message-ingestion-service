from uuid import UUID


class IncomingMessageNotFoundError(Exception):
    def __init__(self, message_id: UUID) -> None:
        super().__init__(f"Incoming message {message_id} was not found")
        self.message_id = message_id


class ActiveClientNotFoundError(Exception):
    def __init__(self, client_id: UUID) -> None:
        super().__init__(f"Active client {client_id} was not found")
        self.client_id = client_id


class InvalidMessageStateError(Exception):
    def __init__(self, *, current_status: str, expected_status: str) -> None:
        super().__init__(
            f"Message status is {current_status!r}; expected {expected_status!r}"
        )
        self.current_status = current_status
        self.expected_status = expected_status
