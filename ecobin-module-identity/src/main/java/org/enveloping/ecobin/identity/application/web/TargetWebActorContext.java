package org.enveloping.ecobin.identity.application.web;

public final class TargetWebActorContext {

    private static final ThreadLocal<TargetWebActor> CURRENT =
            new ThreadLocal<>();

    private TargetWebActorContext() {
    }

    public static void set(TargetWebActor actor) {
        CURRENT.set(actor);
    }

    public static TargetWebActor required() {
        TargetWebActor actor = CURRENT.get();
        if (actor == null) {
            throw new IllegalStateException(
                    "target Web actor is unavailable outside an authenticated request");
        }
        return actor;
    }

    public static void clear() {
        CURRENT.remove();
    }
}
