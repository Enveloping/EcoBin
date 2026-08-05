package org.enveloping.ecobin.recycling.api.port;

import org.enveloping.ecobin.recycling.api.result.PortFullnessAlertFact;

import java.util.List;

/** Supplies current fullness facts without exposing recycling-owned tables. */
public interface RecyclingOperationalAlertSourcePort {

    List<PortFullnessAlertFact> loadPortFullnessAlertFacts();
}
