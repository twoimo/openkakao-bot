// @vitest-environment happy-dom
import { describe, expect, it } from "vitest";
import { parseRuntimeSnapshot } from "../contracts";
import { renderRooms, settingsMarkup } from "../ui";

const room = (chatId: number, extra: Record<string, unknown> = {}) => ({
  chat_id: chatId,
  title: `대화방 ${chatId}`,
  live: true,
  auto_reply: true,
  ...extra,
});

const availableSnapshot = (input: Record<string, unknown>) => {
  const snapshot = parseRuntimeSnapshot({
    available: true,
    context_sync: { mode: "async", waited: false },
    ...input,
  });
  expect(snapshot.available).toBe(true);
  return snapshot;
};

describe("room readiness contract", () => {
  it.each([
    [{ reply_readiness: "ready" }, "ready"],
    [{ replyReadiness: "ready" }, "ready"],
    [{ reply_readiness: "blocked" }, "blocked"],
    [{ replyReadiness: "blocked" }, "blocked"],
    [{ reply_readiness: "unknown" }, "unknown"],
    [{}, "unknown"],
    [{ reply_readiness: true }, "unknown"],
    [{ reply_readiness: 1 }, "unknown"],
    [{ reply_readiness: null }, "unknown"],
    [{ reply_readiness: {} }, "unknown"],
    [{ reply_readiness: ["ready"] }, "unknown"],
    [{ reply_readiness: "READY" }, "unknown"],
    [{ reply_readiness: "ready", replyReadiness: "blocked" }, "unknown"],
    [{ reply_readiness: null, replyReadiness: "ready" }, "unknown"],
    [{ reply_readiness: "ready", replyReadiness: "ready" }, "ready"],
  ])("allows only explicit, non-conflicting readiness: %j", (fields, expected) => {
    const snapshot = availableSnapshot({ rooms: [room(11, fields)] });
    expect(snapshot.rooms[0].replyReadiness).toBe(expected);
  });

  it("does not infer readiness from live, enrollment, or job counts", () => {
    const snapshot = availableSnapshot({
      available: true,
      rooms: [room(11, { open_jobs: 0, delivery_enabled: true, capability_state: "ready" })],
    });
    expect(snapshot.rooms[0].replyReadiness).toBe("unknown");
    expect(snapshot.rooms[0]).not.toHaveProperty("delivery_enabled");
    expect(snapshot.rooms[0]).not.toHaveProperty("capability_state");
  });
});

describe("room readiness display", () => {
  it("counts a live but blocked room as needing attention", () => {
    document.body.innerHTML = settingsMarkup();
    renderRooms(availableSnapshot({
      available: true,
      rooms: [
        room(11, { reply_readiness: "ready" }),
        room(12, { reply_readiness: "ready" }),
        room(13, { reply_readiness: "blocked" }),
      ],
    }));
    expect(document.getElementById("room-summary")?.textContent)
      .toBe("등록된 채팅방 3개 · 답변 가능 2개 · 확인 필요 1개");
    expect(document.getElementById("room-summary")?.getAttribute("aria-live")).toBe("polite");
  });

  it("keeps unknown, stopped, and disabled rooms out of the ready count", () => {
    document.body.innerHTML = settingsMarkup();
    renderRooms(availableSnapshot({
      available: true,
      rooms: [
        room(11),
        room(12, { reply_readiness: "ready", live: false }),
        room(13, { reply_readiness: "ready", auto_reply: false }),
      ],
    }));
    expect(document.getElementById("room-summary")?.textContent)
      .toBe("등록된 채팅방 3개 · 답변 가능 0개 · 확인 필요 2개 · 자동 답변 꺼짐 1개");
  });

  it("does not show retained room data as current after a failed snapshot", () => {
    document.body.innerHTML = settingsMarkup();
    const snapshot = availableSnapshot({
      available: true,
      rooms: [room(11, { reply_readiness: "ready" })],
      available_chats: [room(12)],
    });
    renderRooms(snapshot);
    snapshot.available = false;
    renderRooms(snapshot);
    expect(document.getElementById("room-summary")?.textContent)
      .toBe("채팅방 목록을 불러오지 못했습니다. 다시 확인해 주세요.");
    expect(document.getElementById("room-summary")?.textContent).not.toContain("답변 가능");
    for (const id of ["settings-room-popup"]) {
      const select = document.getElementById(id) as HTMLSelectElement;
      expect(select.options).toHaveLength(1);
      expect(select.value).toBe("");
    }
  });

  it("keeps the empty state distinct from unavailable data", () => {
    document.body.innerHTML = settingsMarkup();
    renderRooms(availableSnapshot({ rooms: [] }));
    expect(document.getElementById("room-summary")?.textContent).toBe("등록된 채팅방이 없습니다.");
  });

  it("updates the summary even when the enrolled selector is absent", () => {
    document.body.innerHTML = '<p id="room-summary"></p>';
    renderRooms(availableSnapshot({ rooms: [room(11)] }));
    expect(document.getElementById("room-summary")?.textContent)
      .toBe("등록된 채팅방 1개 · 답변 가능 0개 · 확인 필요 1개");
  });

  it("preserves selected rooms and option nodes across identical refreshes", () => {
    document.body.innerHTML = settingsMarkup();
    const snapshot = availableSnapshot({
      available: true, rooms: [room(11), room(12)],
      available_chats: [room(11), room(12), room(13)],
    });
    renderRooms(snapshot);
    const enrolled = document.getElementById("settings-room-popup") as HTMLSelectElement;
    enrolled.value = "12";
    const enrolledOption = enrolled.selectedOptions[0];
    const summaryText = document.getElementById("room-summary")!.firstChild;
    for (let n = 0; n < 50; n++) renderRooms(snapshot);
    expect(enrolled.value).toBe("12");
    expect(enrolled.selectedOptions[0]).toBe(enrolledOption);
    expect(document.getElementById("room-summary")!.firstChild).toBe(summaryText);
  });

  it("keeps valid selections after changes when the roster changes", () => {
    document.body.innerHTML = settingsMarkup();
    const snapshot = availableSnapshot({
      available: true, rooms: [room(11), room(12)],
      available_chats: [room(13), room(14)],
    });
    renderRooms(snapshot);
    const enrolled = document.getElementById("settings-room-popup") as HTMLSelectElement;
    enrolled.value = "12";
    snapshot.rooms.reverse();
    snapshot.rooms[0].title = "이름 변경";
    snapshot.availableChats[0].title = "후보 이름 변경";
    renderRooms(snapshot);
    expect(enrolled.value).toBe("12");
    expect(enrolled.selectedOptions[0].textContent).toBe("이름 변경");
    snapshot.rooms.push(...availableSnapshot({ rooms: [room(13)] }).rooms);
    renderRooms(snapshot);
  });
});
