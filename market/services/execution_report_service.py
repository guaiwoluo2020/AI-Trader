"""Canonical execution report application service."""


class ExecutionReportService:
    def __init__(self, repository, event_bus=None):
        self.repository = repository
        self.event_bus = event_bus

    def record(self, user_id, account_id, payload):
        result = self.repository.record(user_id, account_id, payload)
        if self.event_bus is not None:
            from .events import ApplicationEvent, ORDER_EXECUTION_REPORTED
            self.event_bus.publish(ApplicationEvent(
                ORDER_EXECUTION_REPORTED, result or payload,
                int(user_id or 0), int(account_id or 0),
                str((result or payload).get("symbol") or ""),
            ))
        return result

    def status(self, user_id, account_id, instruction_id):
        return self.repository.find_by_instruction(user_id, account_id, instruction_id)
