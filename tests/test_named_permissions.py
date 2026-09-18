import pytest

from codex_thread_bridge.bridge import Bridge
from codex_thread_bridge.ledger import Ledger
from codex_thread_bridge.rpc import AppServer


@pytest.fixture
async def configured_bridge(fake_server, tmp_path):
    _, socket = fake_server
    rpc = AppServer(socket, timeout=1)
    ledger = Ledger(tmp_path / "configured-state" / "operations.sqlite3")
    bridge = Bridge(
        rpc,
        ledger,
        default_permissions="development-profile",
        default_approval_policy="on-request",
        default_approvals_reviewer="auto_review",
    )
    try:
        yield bridge
    finally:
        await rpc.close()
        ledger.close()


async def test_configured_named_profile_is_validated_sent_and_verified(
    configured_bridge, fake_server, tmp_path
):
    fake, _ = fake_server
    receipt = await configured_bridge.create_thread("profile", str(tmp_path), prompt="READY")
    assert receipt["status"] == "accepted"
    assert receipt["requestedPermissions"]["profile"] == "development-profile"
    assert receipt["effectivePermissions"]["profile"] == {"id": "development-profile"}
    start = next(params for method, params in fake.calls if method == "thread/start")
    assert start["permissions"] == "development-profile"
    assert start["approvalPolicy"] == "on-request"
    assert start["approvalsReviewer"] == "auto_review"
    assert "sandbox" not in start and "sandbox_policy" not in start
    methods = [method for method, _ in fake.calls]
    assert methods.index("permissionProfile/list") < methods.index("thread/start")
    assert methods.index("thread/start") < methods.index("turn/start")


async def test_explicit_named_profile_keeps_conservative_approval_default(
    bridge, fake_server, tmp_path
):
    fake, _ = fake_server
    receipt = await bridge.create_thread(
        "profile", str(tmp_path), permissions="development-profile"
    )
    assert receipt["status"] == "accepted"
    start = next(params for method, params in fake.calls if method == "thread/start")
    assert start["approvalPolicy"] == "never"
    assert "approvalsReviewer" not in start


async def test_named_profile_validation_follows_pagination(
    configured_bridge, fake_server, tmp_path, monkeypatch
):
    fake, _ = fake_server
    original_call = configured_bridge.rpc.call
    pages = []

    async def paginated_call(method, params):
        if method != "permissionProfile/list":
            return await original_call(method, params)
        pages.append(params)
        if "cursor" not in params:
            return {
                "data": [{"id": ":read-only", "allowed": True}],
                "nextCursor": "opaque-page-2",
            }
        return {
            "data": [{"id": "development-profile", "allowed": True}],
            "nextCursor": None,
        }

    monkeypatch.setattr(configured_bridge.rpc, "call", paginated_call)
    receipt = await configured_bridge.create_thread("paginated-profile", str(tmp_path))
    assert receipt["status"] == "accepted"
    assert pages == [
        {"cwd": str(tmp_path), "limit": 100},
        {"cwd": str(tmp_path), "limit": 100, "cursor": "opaque-page-2"},
    ]
    assert fake.count("thread/start") == 1


async def test_empty_named_profile_is_rejected_before_listing(bridge, fake_server, tmp_path):
    with pytest.raises(ValueError, match="permissions"):
        await bridge.create_thread("profile", str(tmp_path), permissions="")
    assert not fake_server[0].calls


@pytest.mark.parametrize("profiles", [[], [{"id": "development-profile", "allowed": False}]])
async def test_unavailable_or_disallowed_profile_stops_before_creation(
    configured_bridge, fake_server, tmp_path, profiles
):
    fake, _ = fake_server
    fake.permission_profiles = profiles
    receipt = await configured_bridge.create_thread("profile", str(tmp_path), prompt="WITHHOLD")
    assert receipt["status"] == "failed"
    assert receipt["requestedPermissions"]["profile"] == "development-profile"
    assert fake.count("thread/start") == 0
    assert fake.count("turn/start") == 0


async def test_returned_profile_mismatch_retains_thread_and_withholds_prompt(
    configured_bridge, fake_server, tmp_path
):
    fake, _ = fake_server
    fake.override_creation = {"activePermissionProfile": {"id": ":workspace"}}
    receipt = await configured_bridge.create_thread("profile", str(tmp_path), prompt="WITHHOLD")
    assert receipt["status"] == "failed"
    assert receipt["threadId"] == "thread-1"
    assert receipt["effectivePermissions"]["profile"] == {"id": ":workspace"}
    assert fake.count("turn/start") == 0


@pytest.mark.parametrize(
    "override",
    [{"approvalPolicy": "never"}, {"approvalsReviewer": "user"}],
)
async def test_returned_approval_mismatch_retains_thread_and_withholds_prompt(
    configured_bridge, fake_server, tmp_path, override
):
    fake, _ = fake_server
    fake.override_creation = override
    receipt = await configured_bridge.create_thread("approval", str(tmp_path), prompt="WITHHOLD")
    assert receipt["status"] == "failed"
    assert receipt["threadId"] == "thread-1"
    assert fake.count("turn/start") == 0


@pytest.mark.parametrize(
    "legacy",
    [
        {"sandbox": "read-only"},
        {"sandbox_policy": {"type": "readOnly", "networkAccess": False}},
    ],
)
async def test_explicit_named_profile_rejects_legacy_inputs(bridge, fake_server, tmp_path, legacy):
    with pytest.raises(ValueError, match="cannot be combined"):
        await bridge.create_thread(
            "mixed", str(tmp_path), permissions="development-profile", **legacy
        )
    assert not fake_server[0].calls


async def test_explicit_legacy_read_only_overrides_configured_profile(
    configured_bridge, fake_server, tmp_path
):
    fake, _ = fake_server
    receipt = await configured_bridge.create_thread("legacy", str(tmp_path), sandbox="read-only")
    assert receipt["status"] == "accepted"
    start = next(params for method, params in fake.calls if method == "thread/start")
    assert start["sandbox"] == "read-only" and "permissions" not in start
    assert fake.count("permissionProfile/list") == 0


async def test_replay_retains_resolved_profile_without_resending(
    configured_bridge, fake_server, tmp_path
):
    fake, _ = fake_server
    first = await configured_bridge.create_thread("stable", str(tmp_path))
    replay = await configured_bridge.create_thread("stable", str(tmp_path))
    assert replay["replayed"]
    assert replay["requestedPermissions"] == first["requestedPermissions"]
    assert fake.count("permissionProfile/list") == 1
    assert fake.count("thread/start") == 1


async def test_profile_contract_represents_repository_metadata_access(
    configured_bridge, fake_server, tmp_path
):
    fake, _ = fake_server
    receipt = await configured_bridge.create_thread("metadata", str(tmp_path))
    assert receipt["effectivePermissions"]["profile"] == {"id": "development-profile"}
    assert receipt["effectivePermissions"]["sandbox"]["type"] != "dangerFullAccess"
    start = next(params for method, params in fake.calls if method == "thread/start")
    assert "sandbox" not in start
