from amocrm_service.amocrm.errors import AmoCRMEntityNotFound

__all__ = ["AmoCRMClient", "AmoCRMEntityNotFound"]


def __getattr__(name: str):
    if name == "AmoCRMClient":
        from amocrm_service.amocrm.client import AmoCRMClient

        return AmoCRMClient
    raise AttributeError(name)
