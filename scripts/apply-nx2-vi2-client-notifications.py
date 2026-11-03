#!/usr/bin/env python3
"""Build 65: port vi2 client-lifecycle notifications from SX2A v0.3.2.

The pinned Eglantine revision auto-stubs vi2 command 201, and returns a fresh
never-signalled event for command 11. SX2A v0.3.2 tracks client tokens and
delivers notifications for 202 and 102. Preserve our newer 31/32 native-event
model and existing 212 available-buffer mask.
"""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply-nx2-vi2-client-notifications.py <upstream-dir>")
root = Path(sys.argv[1]).resolve()
path = root / "src/core/hle/service/nx2_gsv.h"
source = path.read_text()
start = source.find("class INx2Vi2Session final :")
end = source.find("\nclass ", start + 10)
if start < 0 or end < 0:
    raise SystemExit("Cannot isolate INx2Vi2Session")
old = source[start:end]
data = old

def change_once(anchor, replacement, description):
    global data
    if replacement in data:
        print("Already applied:", description)
        return
    count = data.count(anchor)
    if count != 1:
        raise SystemExit(f"{description}: expected exactly one anchor, found {count}")
    data = data.replace(anchor, replacement, 1)
    print("Applied:", description)

change_once(
    '{11, &INx2Vi2Session::ReturnEvent, "Unknown11"},',
    '{11, &INx2Vi2Session::GetNotificationEvent, "GetNotificationEvent"},\n'
    '            {21, &INx2Vi2Session::GetNotifications, "GetNotifications"},',
    "Persistent notification event + bitmap handler",
)
change_once(
    '{101, &INx2Vi2Session::StubReplyZeroed, "Unknown101"},',
    '{101, &INx2Vi2Session::OpenDisplay, "OpenDisplay"},',
    "Display registration",
)
change_once(
    '{102, &INx2Vi2Session::StubReplyZeroed, "Unknown102"},',
    '{102, &INx2Vi2Session::CloseDisplay, "CloseDisplay"},',
    "Display retirement notification",
)
change_once(
    '{202, &INx2Vi2Session::StubReplyZeroed, "Unknown202"},',
    '{201, &INx2Vi2Session::RegisterClientState, "RegisterClientState"},\n'
    '            {202, &INx2Vi2Session::UnregisterClientState, "UnregisterClientState"},',
    "TIPC 201/202 client lifecycle",
)

# Implement exactly the lifecycle contracts used by the v0.3.2 build.
# Keep original 31/32 native-event model and existing GetLayerBufferBits(212),
# which already correctly returns 0x7fff in this pinned Eglantine revision.
body_marker = "    void ReturnEvent(HLERequestContext& ctx) {\n"
if "void RegisterClientState(HLERequestContext& ctx)" not in data:
    if data.count(body_marker) != 1:
        raise SystemExit("Could not find vi2 event handler insertion point")
    implementations = r'''    // SX2A v0.3.2: command 11 returns a persistent notification event.
    // A fresh event every call was never signalled after client teardown.
    void GetNotificationEvent(HLERequestContext& ctx) {
        if (!m_notification_event) {
            m_notification_event =
                m_context.CreateEvent(fmt::format("nx2:vi2:Notify{}", m_events.size()));
            m_events.push_back(m_notification_event);
        }
        if (AnyNotification()) {
            m_notification_event->Signal(system.Kernel());
        }
        IPC::ResponseBuilder rb{ctx, 2, 1, 0};
        rb.Push(ResultSuccess);
        rb.PushCopyObjects(ctx, m_notification_event->GetReadableEvent());
    }

    // Command 21 copies up to 1024 notification bits into the caller buffer.
    // A complete read drains the bitmap and clears the persistent event.
    void GetNotifications(HLERequestContext& ctx) {
        const auto size = std::min<std::size_t>(ctx.GetWriteBufferSize(),
                                                 sizeof(m_notifications));
        if (size != 0) {
            ctx.WriteBuffer(m_notifications.data(), size);
        }
        if (size >= sizeof(m_notifications)) {
            m_notifications.fill(0);
            if (m_notification_event) {
                m_notification_event->Clear(system.Kernel());
            }
        }
        IPC::ResponseBuilder rb{ctx, 2};
        rb.Push(ResultSuccess);
    }

    // Command 101 registers the display's retirement-notification index.
    void OpenDisplay(HLERequestContext& ctx) {
        m_display_notification = ctx.CommandBuffer()[4];
        IPC::ResponseBuilder rb{ctx, 2};
        rb.Push(ResultSuccess);
    }

    // Command 102 reports that the display was retired.
    void CloseDisplay(HLERequestContext& ctx) {
        Notify(m_display_notification);
        IPC::ResponseBuilder rb{ctx, 2};
        rb.Push(ResultSuccess);
    }

    // 201(u64 token, u32 retire_index, u32 available_index).
    void RegisterClientState(HLERequestContext& ctx) {
        const auto* w = ctx.CommandBuffer();
        const u64 token = u64{w[2]} | (u64{w[3]} << 32);
        if (token != 0 && w[4] < m_notifications.size() * 32 &&
            w[5] < m_notifications.size() * 32 && w[4] != w[5]) {
            m_clients[token] = w[4];
            LOG_INFO(Service, "NX2_VI2_CLIENT_REGISTER token={:#x} retire={} available={}",
                     token, w[4], w[5]);
        } else {
            LOG_WARNING(Service,
                        "NX2_VI2_CLIENT_REGISTER ignored token={:#x} retire={} available={}",
                        token, w[4], w[5]);
        }
        IPC::ResponseBuilder rb{ctx, 2};
        rb.Push(ResultSuccess);
    }

    // 202(u64 token): wake the render thread awaiting client retirement.
    void UnregisterClientState(HLERequestContext& ctx) {
        const auto* w = ctx.CommandBuffer();
        const u64 token = u64{w[2]} | (u64{w[3]} << 32);
        const auto client = m_clients.find(token);
        if (client != m_clients.end()) {
            LOG_INFO(Service, "NX2_VI2_CLIENT_RETIRE token={:#x} notification={}",
                     token, client->second);
            Notify(client->second);
            m_clients.erase(client);
        }
        IPC::ResponseBuilder rb{ctx, 2};
        rb.Push(ResultSuccess);
    }

    bool AnyNotification() const {
        return std::any_of(m_notifications.begin(), m_notifications.end(),
                           [](u32 bits) { return bits != 0; });
    }

    void Notify(u32 index) {
        if (index >= m_notifications.size() * 32) {
            return;
        }
        m_notifications[index / 32] |= 1U << (index % 32);
        if (m_notification_event) {
            m_notification_event->Signal(system.Kernel());
        }
    }

'''
    data = data.replace(body_marker, implementations + body_marker, 1)
    print("Applied: client state and notification handlers")

field_marker = "    std::vector<Kernel::KEvent*> m_events;\n"
fields = (
    "    std::vector<Kernel::KEvent*> m_events;\n"
    "    std::map<u64, u32> m_clients; // token -> retire notification index\n"
    "    std::array<u32, 32> m_notifications{};\n"
    "    u32 m_display_notification{};\n"
    "    Kernel::KEvent* m_notification_event{};\n"
)
change_once(field_marker, fields, "Per-session lifecycle state")

for required in (
    '{201, &INx2Vi2Session::RegisterClientState, "RegisterClientState"}',
    '{202, &INx2Vi2Session::UnregisterClientState, "UnregisterClientState"}',
    '{21, &INx2Vi2Session::GetNotifications, "GetNotifications"}',
    'void GetNotificationEvent(HLERequestContext& ctx)',
    'void UnregisterClientState(HLERequestContext& ctx)',
    "NX2_VI2_CLIENT_REGISTER",
    "NX2_VI2_CLIENT_RETIRE",
    '{212, &INx2Vi2Session::GetLayerBufferBits, "GetLayerBufferBits"}',
    '{31, &INx2Vi2Session::AcquireNativeEvent, "AcquireNativeEvent"}',
):
    if required not in data:
        raise SystemExit(f"Missing vi2 contract after patch: {required}")

if data != old:
    path.write_text(source[:start] + data + source[end:])
print("Build 65 vi2 client notifications ported without replacing existing native events")
