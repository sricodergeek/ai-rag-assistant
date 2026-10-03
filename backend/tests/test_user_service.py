import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from backend.app.models import User
from backend.app.user_service import get_or_create_google_user


class FakeQuery:
    def __init__(self, session):
        self.session = session
        self.filters = {}

    def filter_by(self, **filters):
        self.filters = filters
        return self

    def first(self):
        return self.session.find_user(self.filters)


class FakeSession:
    def __init__(self, users=None):
        self.users = list(users or [])
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.refreshes = []
        self.is_broken = False

    def query(self, model):
        assert model is User
        return FakeQuery(self)

    def find_user(self, filters):
        assert not self.is_broken
        return next(
            (
                user
                for user in self.users
                if user.auth_provider == filters["auth_provider"]
                and user.provider_user_id == filters["provider_user_id"]
            ),
            None,
        )

    def add(self, user):
        self.added.append(user)

    def commit(self):
        self.commits += 1
        user = self.added[-1]
        user.id = uuid.uuid4()
        user.created_at = datetime.now(timezone.utc)
        self.users.append(user)

    def rollback(self):
        self.rollbacks += 1
        self.is_broken = False

    def refresh(self, user):
        self.refreshes.append(user)


def test_existing_google_user_is_returned_without_overwriting_profile():
    existing_user = User(
        id=uuid.uuid4(),
        email="stored@example.test",
        name="Stored Name",
        avatar_url="https://example.test/stored.png",
        auth_provider="google",
        provider_user_id="synthetic-google-sub-1",
    )
    db = FakeSession([existing_user])

    result = get_or_create_google_user(
        db,
        {
            "sub": "synthetic-google-sub-1",
            "email": "different-synthetic-email@example.test",
            "name": "New Name",
            "picture": "https://example.test/new.png",
        },
    )

    assert result is existing_user
    assert result.email == "stored@example.test"
    assert result.name == "Stored Name"
    assert result.avatar_url == "https://example.test/stored.png"
    assert db.added == []
    assert db.commits == 0


def test_new_google_user_is_created_with_identity_fields():
    db = FakeSession()
    identity = {
        "sub": "synthetic-google-sub-2",
        "email": "new-user@example.test",
        "name": "Synthetic User",
        "picture": "https://example.test/avatar.png",
    }

    user = get_or_create_google_user(db, identity)

    assert user.email == identity["email"]
    assert user.auth_provider == "google"
    assert user.provider_user_id == identity["sub"]
    assert user.name == identity["name"]
    assert user.avatar_url == identity["picture"]
    assert user.id is not None
    assert db.commits == 1
    assert db.refreshes == [user]


def test_same_google_sub_does_not_create_duplicate_users():
    db = FakeSession()
    identity = {
        "sub": "synthetic-google-sub-3",
        "email": "repeat@example.test",
    }

    first_user = get_or_create_google_user(db, identity)
    second_user = get_or_create_google_user(db, identity)

    assert second_user is first_user
    assert len(db.users) == 1
    assert len(db.added) == 1
    assert db.commits == 1


def test_name_and_picture_can_be_absent():
    db = FakeSession()

    user = get_or_create_google_user(
        db,
        {
            "sub": "synthetic-google-sub-4",
            "email": "minimal@example.test",
        },
    )

    assert user.email == "minimal@example.test"
    assert user.name is None
    assert user.avatar_url is None


class RacingSession(FakeSession):
    def __init__(self, racing_user):
        super().__init__()
        self.racing_user = racing_user
        self.lookup_count = 0

    def find_user(self, filters):
        self.lookup_count += 1
        if self.lookup_count == 1:
            return None
        return self.racing_user

    def commit(self):
        self.commits += 1
        self.is_broken = True
        raise IntegrityError("INSERT user", {}, RuntimeError("synthetic unique race"))


def test_unique_constraint_race_rolls_back_and_returns_winning_user():
    identity = {
        "sub": "synthetic-google-sub-5",
        "email": "racing@example.test",
    }
    winning_user = User(
        id=uuid.uuid4(),
        email=identity["email"],
        auth_provider="google",
        provider_user_id=identity["sub"],
    )
    db = RacingSession(winning_user)

    result = get_or_create_google_user(db, identity)

    assert result is winning_user
    assert db.rollbacks == 1
    assert db.lookup_count == 2
    assert db.is_broken is False
