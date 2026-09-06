-- V68: align the immutable software-fact invariant with the image bridge.
--
-- A freshly accepted image can truthfully run its embedded business program
-- before any independently published business release has been installed.
-- V67 made that source a valid deployment baseline, while the original V63
-- fact-table constraint still required an active release UID for every ready
-- business process.  Permit the release-less fact only when the permanent
-- communication agent and updater both carry strict image component versions
-- from the same image generation.  All process and negotiated-protocol gates
-- remain mandatory.

ALTER TABLE dev_device_software_fact
    DROP CHECK ck_dev_software_fact_process,
    ADD CONSTRAINT ck_dev_software_fact_process CHECK (
        business_process_state IN (
            'STOPPED', 'STARTING', 'RUNNING', 'FAILED'
        )
        AND business_process_ready IN (0, 1)
        AND (
            business_process_ready = 0
            OR (
                business_process_state = 'RUNNING'
                AND negotiated_communication_business_major IS NOT NULL
                AND negotiated_updater_business_major IS NOT NULL
                AND (
                    active_business_release_uid IS NOT NULL
                    OR (
                        active_business_release_uid IS NULL
                        AND communication_agent_version REGEXP
                            '^communication-[0-9]{8}-[0-9]{2,6}$'
                        AND device_updater_version REGEXP
                            '^updater-[0-9]{8}-[0-9]{2,6}$'
                        AND BINARY SUBSTRING(
                            communication_agent_version, 15
                        ) = BINARY SUBSTRING(device_updater_version, 9)
                    )
                )
            )
        )
    );
