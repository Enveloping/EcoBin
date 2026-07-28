package org.enveloping.ecobin.identity.application.miniapp;

public final class TargetMiniappActorContext {

    private static final ThreadLocal<TargetMiniappActor> CURRENT =
            new ThreadLocal<>();

    private TargetMiniappActorContext() {
    }

    public static void set(TargetMiniappActor actor) {
        if (CURRENT.get() != null) {
            throw new IllegalStateException(
                    "target miniapp actor is already established");
        }
        CURRENT.set(actor);
    }

    public static TargetMiniappActor required() {
        TargetMiniappActor actor = CURRENT.get();
        if (actor == null) {
            throw new IllegalStateException(
                    "target miniapp actor is not established");
        }
        return actor;
    }

    public static void clear() {
        CURRENT.remove();
    }
}
