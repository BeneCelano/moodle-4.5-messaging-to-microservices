"""Use cases for contacts, contact requests and blocks."""

from __future__ import annotations

from collections.abc import Callable

from ..domain.entities import Contact, ContactRequest, UserBlock
from ..domain.events import contact_request_created
from ..domain.ports import Clock, UnitOfWork


class ContactService:
    """Use cases of contacts, requests and blocks."""

    def __init__(self, uow_factory: Callable[[], UnitOfWork], clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def add_contact(self, user_id: int, contact_id: int) -> Contact:
        """Create the contact relationship between two users."""
        async with self._uow_factory() as uow:
            contact = await uow.contacts.add_contact(
                Contact(user_id=user_id, contact_id=contact_id, time_created=self._clock.now())
            )
            await uow.commit()

        return contact

    async def remove_contact(self, user_id: int, contact_id: int) -> Contact | None:
        """Remove the contact relationship between two users."""
        async with self._uow_factory() as uow:
            contact = await uow.contacts.get_contact(user_id, contact_id)
            if contact is None:
                return None

            await uow.contacts.delete_contact(int(contact.id))
            await uow.commit()

        return contact

    async def get_contact(self, user_id: int, contact_id: int) -> Contact | None:
        """Return the contact relationship between two users."""
        async with self._uow_factory() as uow:
            return await uow.contacts.get_contact(user_id, contact_id)

    async def count_contacts(self, user_id: int) -> int:
        async with self._uow_factory() as uow:
            return await uow.contacts.count_contacts(user_id)

    async def list_contact_ids(self, user_id: int, offset: int = 0, limit: int = 0) -> list[int]:
        """List the identifiers of the users in contact with the given one."""
        async with self._uow_factory() as uow:
            return await uow.contacts.list_contact_ids(user_id, offset, limit)

    async def list_blocked_user_ids(self, user_id: int) -> list[int]:
        async with self._uow_factory() as uow:
            return await uow.contacts.list_blocked_user_ids(user_id)

    async def list_received_request_user_ids(
        self, user_id: int, offset: int = 0, limit: int = 0
    ) -> list[int]:
        """List who asked the given user for contact, excluding those the user blocked.
        """
        async with self._uow_factory() as uow:
            return await uow.contacts.list_received_request_user_ids(user_id, offset, limit)

    async def block(self, user_id: int, blocked_user_id: int) -> UserBlock:
        """Record the block of a user."""
        async with self._uow_factory() as uow:
            block = await uow.contacts.add_block(
                UserBlock(
                    user_id=user_id, blocked_user_id=blocked_user_id, time_created=self._clock.now()
                )
            )
            await uow.commit()

        return block

    async def unblock(self, user_id: int, blocked_user_id: int) -> UserBlock | None:
        """Remove the block of a user."""
        async with self._uow_factory() as uow:
            block = await uow.contacts.get_block(user_id, blocked_user_id)
            if block is None:
                return None

            await uow.contacts.delete_block(int(block.id))
            await uow.commit()

        return block

    async def is_blocked(self, user_id: int, blocked_user_id: int) -> bool:
        """Whether the user has blocked the other."""
        async with self._uow_factory() as uow:
            return await uow.contacts.get_block(user_id, blocked_user_id) is not None

    async def create_request(
        self, user_id: int, requested_user_id: int, correlation_id: str | None = None
    ) -> ContactRequest:
        """Create a contact request and record the event announcing it."""
        now = self._clock.now()

        async with self._uow_factory() as uow:
            existing = await uow.contacts.get_request(user_id, requested_user_id)
            if existing is not None:
                return existing

            request = await uow.contacts.add_request(
                ContactRequest(
                    user_id=user_id, requested_user_id=requested_user_id, time_created=now
                )
            )

            uow.events.collect(
                contact_request_created(
                    request_id=int(request.id),
                    user_id=user_id,
                    requested_user_id=requested_user_id,
                    time_created=now,
                    correlation_id=correlation_id,
                )
            )
            await uow.commit()

        return request

    async def confirm_request(self, user_id: int, requested_user_id: int) -> Contact | None:
        """Confirm a contact request, turning it into a contact relationship."""
        async with self._uow_factory() as uow:
            request = await uow.contacts.get_request(user_id, requested_user_id)
            if request is None:
                return None

            contact = await uow.contacts.add_contact(
                Contact(
                    user_id=user_id, contact_id=requested_user_id, time_created=self._clock.now()
                )
            )
            await uow.contacts.delete_request(int(request.id))
            await uow.commit()

        return contact

    async def decline_request(self, user_id: int, requested_user_id: int) -> bool:
        async with self._uow_factory() as uow:
            request = await uow.contacts.get_request(user_id, requested_user_id)
            if request is None:
                return False

            await uow.contacts.delete_request(int(request.id))
            await uow.commit()

        return True

    async def requests_between(self, user_id: int, requested_user_id: int) -> list[ContactRequest]:
        """Return the contact requests between two users, in both directions."""
        async with self._uow_factory() as uow:
            return await uow.contacts.list_requests_between(user_id, requested_user_id)

    async def count_received_requests(self, user_id: int) -> int:
        """Count the requests received by a user, excluding those from users they
        blocked.
        """
        async with self._uow_factory() as uow:
            return await uow.contacts.count_received_requests(user_id)
