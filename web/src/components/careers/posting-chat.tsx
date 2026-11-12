"use client";

import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { ApplyChat } from "@/components/chat/apply-chat";
import type { StartContext } from "@/lib/types";
import { cn } from "@/lib/utils";
import type { Posting } from "./postings";

/** The apply card's "any location" value. */
export const ANY_LOCATION = "any";

type PostingChatState = {
  location: string;
  setLocation: (location: string) => void;
  openChat: () => void;
};

const PostingChatContext = createContext<PostingChatState | null>(null);

export function usePostingChat(): PostingChatState {
  const value = useContext(PostingChatContext);
  if (!value) throw new Error("usePostingChat outside PostingChat");
  return value;
}

/** Opens the chat from a link's click, leaving modified clicks (new tab) to the link's own href. */
export function openOnPlainClick(event: React.MouseEvent, openChat: () => void) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  openChat();
}

/** The panel sits beside the posting from 64 rem; below that it covers the page (see globals.css). */
const BESIDE = "(min-width: 64rem)";

function subscribeBeside(onChange: () => void) {
  const media = window.matchMedia(BESIDE);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

/** Whether the panel sits beside the posting; on the server, assume a phone. */
function useBeside(): boolean {
  return useSyncExternalStore(
    subscribeBeside,
    () => window.matchMedia(BESIDE).matches,
    () => false,
  );
}

function query(id: string | null, context: StartContext): string {
  if (id) return `?c=${encodeURIComponent(id)}`;
  return context.location_id ? `?apply=1&location_id=${encodeURIComponent(context.location_id)}` : "?apply=1";
}

/**
 * The candidate chat inside a job posting: a panel docked to the bottom right from 1024 px, full
 * screen below.
 *
 * The URL carries the open chat (`?apply=1`, then `?c=<id>` once the conversation exists), so a
 * refresh or a shared link reopens it. Closing hides the panel without unmounting it, so
 * reopening resumes the same conversation.
 */
export function PostingChat({ posting, children }: { posting: Posting; children: React.ReactNode }) {
  const [location, setLocation] = useState(ANY_LOCATION);
  const [open, setOpen] = useState(false);
  // Set on the first open and kept: the chat stays mounted while the panel is closed.
  const [chat, setChat] = useState<{ conversationId?: string; context: StartContext } | null>(null);
  const openRef = useRef(false);
  const idRef = useRef<string | null>(null);
  const contextRef = useRef<StartContext | null>(null);
  // Whether the open chat has its own history entry, so closing it is a step back.
  const pushed = useRef(false);
  const opener = useRef<HTMLElement | null>(null);
  const panel = useRef<HTMLElement>(null);
  const headingId = useId();
  const beside = useBeside();

  const contextFor = useCallback(
    (locationId: string | null): StartContext => ({
      country: posting.country,
      ...(locationId ? { location_id: locationId } : {}),
      ...(posting.lang === "en" ? { locale: "en" } : {}),
    }),
    [posting.country, posting.lang],
  );

  const show = useCallback((next: { conversationId?: string; context: StartContext } | null, visible: boolean) => {
    if (next && !contextRef.current) {
      contextRef.current = next.context;
      idRef.current = next.conversationId ?? null;
      setChat(next);
    }
    openRef.current = visible;
    setOpen(visible);
  }, []);

  // The URL decides on load and on back/forward.
  useEffect(() => {
    const sync = () => {
      const params = new URLSearchParams(window.location.search);
      const id = params.get("c");
      const wanted = Boolean(id) || params.has("apply");
      show(
        wanted ? { conversationId: id ?? undefined, context: contextFor(params.get("location_id")?.slice(0, 64) ?? null) } : null,
        wanted,
      );
    };
    queueMicrotask(sync);
    // Back or forward onto an entry with the chat open: closing it steps back again.
    const onPop = () => {
      const wasOpen = openRef.current;
      sync();
      pushed.current = openRef.current;
      if (wasOpen && !openRef.current) opener.current?.focus({ preventScroll: true });
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [contextFor, show]);

  const openChat = useCallback(() => {
    if (openRef.current) {
      panel.current?.focus({ preventScroll: true });
      return;
    }
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const context = contextRef.current ?? contextFor(location === ANY_LOCATION ? null : location);
    show({ context }, true);
    window.history.pushState(null, "", window.location.pathname + query(idRef.current, context));
    pushed.current = true;
  }, [contextFor, location, show]);

  const close = useCallback(() => {
    show(null, false);
    if (pushed.current) {
      pushed.current = false;
      window.history.back();
    } else {
      window.history.replaceState(null, "", window.location.pathname);
    }
    opener.current?.focus({ preventScroll: true });
  }, [show]);

  const onConversation = useCallback((id: string | null) => {
    idRef.current = id;
    if (openRef.current && contextRef.current) {
      window.history.replaceState(null, "", window.location.pathname + query(id, contextRef.current));
    }
  }, []);

  useEffect(() => {
    if (open) panel.current?.focus({ preventScroll: true });
  }, [open]);

  // Escape closes the panel. A native listener, so an Escape that closes a widget's popup (rendered
  // in a portal outside the panel) does not reach it.
  useEffect(() => {
    const el = panel.current;
    if (!el || !open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) close();
    };
    el.addEventListener("keydown", onKeyDown);
    return () => el.removeEventListener("keydown", onKeyDown);
  }, [open, close]);

  const value = useMemo(() => ({ location, setLocation, openChat }), [location, openChat]);

  return (
    <PostingChatContext.Provider value={value}>
      {/* Full screen on a phone, the panel is modal: the posting behind is out of reach. */}
      {/* Beside the posting, the page gives up 420 px on the right; its header and footer still span the window. */}
      <div
        data-chat-open={open || undefined}
        inert={(open && !beside) || undefined}
        className={cn("group/posting @container flex min-h-full flex-1 flex-col", open && "lg:pr-[420px]")}
      >
        {children}
      </div>
      {chat && (
        <aside
          ref={panel}
          aria-labelledby={headingId}
          tabIndex={-1}
          data-chat-panel={open ? "open" : "closed"}
          className={cn(
            "fixed inset-0 z-50 flex-col bg-background outline-none",
            // Beside the posting: a panel docked to the bottom right, inside the 420 px the posting gives up.
            "lg:top-auto lg:right-4 lg:left-auto lg:h-[min(720px,calc(100dvh-5rem))] lg:w-[404px] lg:overflow-hidden lg:rounded-t-2xl lg:border lg:border-b-0",
            "duration-200 ease-out animate-in fade-in-0 slide-in-from-bottom-6 motion-reduce:animate-none lg:slide-in-from-right-8 lg:slide-in-from-bottom-0",
            open ? "flex" : "hidden",
          )}
        >
          <ApplyChat
            conversationId={chat.conversationId}
            posting={chat.context}
            onConversation={onConversation}
            onBack={close}
            open={open}
            headingId={headingId}
          />
        </aside>
      )}
    </PostingChatContext.Provider>
  );
}

/** A link that opens the posting's chat; without JavaScript it still goes to `href`. */
export function OpenChatLink(props: Omit<React.ComponentProps<"a">, "onClick">) {
  const { openChat } = usePostingChat();
  return <a {...props} onClick={(event) => openOnPlainClick(event, openChat)} />;
}
