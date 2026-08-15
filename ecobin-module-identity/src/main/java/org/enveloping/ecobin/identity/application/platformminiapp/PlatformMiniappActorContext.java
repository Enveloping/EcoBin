package org.enveloping.ecobin.identity.application.platformminiapp;

public final class PlatformMiniappActorContext {

    private static final ThreadLocal<PlatformMiniappActor> CURRENT =
            new ThreadLocal<>();

    private PlatformMiniappActorContext() {
    }

    public static void set(PlatformMiniappActor actor) {
        if (CURRENT.get() != null) {
            throw new IllegalStateException(
                    "platform miniapp actor is already established");
        }
        CURRENT.set(actor);
    }

    public static PlatformMiniappActor required() {
        PlatformMiniappActor actor = CURRENT.get();
        if (actor == null) {
            throw new IllegalStateException(
                    "platform miniapp actor is not established");
        }
        return actor;
    }

    public static void clear() {
        CURRENT.remove();
    }
}
