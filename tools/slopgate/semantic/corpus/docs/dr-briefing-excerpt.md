# Dominican Republic: proposed PDPP reference implementation

Internal technical briefing, 17 September 2026.

## 1. Proposed implementation

Extend the Dominican Republic's citizen-data infrastructure with citizen-controlled encrypted storage and PDPP-authorized access. Government institutions remain the authoritative sources of their records. Agencies deliver signed, versioned copies into citizen storage. Each citizen's Personal Server enforces access grants and serves authorized data to applications.

Soy Yo RD provides the citizen entry point. The first integration uses a government portal on a `gob.do` domain, opened from Soy Yo. Cuenta Unica Ciudadana (CUC) authenticates the citizen. Vana supplies the Personal Server and pilot integrations.

PDPP complements the national interoperability platform and Soy Yo RD. It adds a citizen-directed sharing channel for public services, private applications and artificial intelligence agents. It replaces neither system.

## 6. Architecture confidence

The main flow uses citizen-held encrypted copies, government authentication, citizen approval verified inside the Personal Server, and DR-hosted storage. Agencies remain authoritative. These are the baseline design choices.

Three architectural details need review: the location of durable authorization state within the enclave service; the policy that binds signing to the citizen's exact approval; and the key-management and adapter boundaries that allow a wallet or infrastructure provider to be replaced.

DR's identity interfaces, agency APIs, and hosting requirements determine the deployment configuration. A DR-operated enclave fleet requires a separate assessment of attestation, operations and cost.
