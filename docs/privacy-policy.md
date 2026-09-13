---
status: DRAFT — requires review by a qualified legal professional before publication.
jurisdiction: India
last_updated: 2026-09-13
---

# Privacy Policy — Reconcile

> **⚠ DRAFT NOTICE:** This is a draft privacy policy prepared for review purposes. It has not been reviewed by a lawyer. **Do not publish this document without independent legal review**, particularly given that this product handles financial transaction data and likely qualifies as a "Data Fiduciary" under India's Digital Personal Data Protection Act, 2023 (DPDP Act). See the Risk Flag List at the end of this document.

---

## 1. Who we are

Reconcile ("the Service", "we", "us", "our") is operated by:

**[OPERATOR NAME — to be filled in before publication]**  
Address: **[BUSINESS ADDRESS — to be filled in before publication]**  
Contact: **[EMAIL ADDRESS — to be filled in before publication]**

The operator is an individual, not a registered business entity.

---

## 2. Scope of this policy

This policy describes how personal data is collected, used, stored, and protected when you use Reconcile — an agentic bookkeeping tool that ingests bank transactions via Plaid, categorises them using an AI agent, and posts approved entries to QuickBooks.

At the time of writing, Reconcile is not open for public signup. Access is limited to a single administrator account seeded at deployment. This policy describes the intended data-handling practices that will apply once access is opened more broadly.

---

## 3. Legal basis — India Digital Personal Data Protection Act, 2023 (DPDP Act)

The operator processes personal data in accordance with the **Digital Personal Data Protection Act, 2023 (DPDP Act)** of India. Processing is carried out on the basis of **consent** provided at the time of account creation and use of the Service.

> **⚠ LEGAL FLAG:** An individual who processes others' financial data in the course of a service — even informally — may qualify as a **"Data Fiduciary"** under Section 2(i) of the DPDP Act. Data Fiduciaries must, among other things, (a) process data only for the purpose for which consent was given, (b) implement reasonable security safeguards, and (c) provide Data Principals with rights of access and correction. **This determination must be confirmed with an actual lawyer before publication.**

---

## 4. What personal data we collect

| Category | Specific data | Purpose |
|---|---|---|
| Account credentials | Email address, bcrypt-hashed password | Authentication and account management |
| Bank transaction data | Vendor/merchant name, transaction amount, transaction date | Core reconciliation and categorisation workflow |
| Categorisation data | Proposed vendor, category, confidence score, agent reasoning | Building and improving vendor-matching memory |
| Vendor memory (learned rules) | Raw vendor pattern, canonical vendor name, category | Improving future categorisation accuracy for the account |
| Correction records | Human-supplied vendor and category corrections | Training the agent's memory |
| Audit log | Event type, actor, event detail, timestamp | Compliance record-keeping and audit trail |

We do **not** collect:
- Payment card numbers or bank account numbers
- Government-issued identification numbers (Aadhaar, PAN, passport)
- Biometric data
- Precise location data
- Any data from minors

---

## 5. How data is collected

- **Directly from you:** email address and password at account creation; human corrections you supply via the review interface.
- **Via Plaid:** bank transaction data is fetched from Plaid's Sandbox API using credentials you configure. Plaid's own privacy practices apply to data passed through their service; please review [Plaid's Privacy Policy](https://plaid.com/legal/privacy-statement/).
- **Via the AI agent:** the Groq language model API receives transaction data to produce categorisation proposals. Groq's data handling practices apply; please review [Groq's Privacy Policy](https://groq.com/privacy-policy/).
- **Via QuickBooks:** approved journal entries are posted to Intuit QuickBooks using credentials you configure. Intuit's own privacy practices apply; please review [Intuit's Privacy Policy](https://www.intuit.com/privacy/statement/).

---

## 6. Third-party services that receive data

| Third party | Data shared | Purpose |
|---|---|---|
| Plaid | Plaid API credentials (you provide) | Fetching bank transaction data |
| Groq | Transaction vendor/amount/date; audit context | AI-powered categorisation |
| QuickBooks (Intuit) | Approved journal entry data | Ledger synchronisation |
| Google Fonts API | Your IP address (via font load requests) | Loading DM Sans and Space Mono typefaces for the interface |

> **Note on Google Fonts:** The application loads fonts from `fonts.googleapis.com`. This results in your IP address being transmitted to Google as a side effect of the HTTP request. No cookies are set by this request, and no user-identifying data other than IP is sent. The operator is considering self-hosting fonts to eliminate this data transfer in a future version.

---

## 7. How long we retain data

| Data | Retention |
|---|---|
| Account credentials (email, hashed password) | Until you request deletion of your account |
| Transaction data, audit log, agent decisions | Until you trigger a data reset or request deletion |
| Vendor-memory rules | Until you trigger a data reset or request deletion |

The operator does not currently operate an automated retention schedule. Retention periods will be formalised before wider launch.

---

## 8. Your rights under the DPDP Act

As a Data Principal under the DPDP Act, you have the right to:

1. **Access** — request a summary of the personal data held about you.
2. **Correction and completion** — request that inaccurate data is corrected.
3. **Erasure** — request deletion of your personal data where the purpose of processing has ended or consent has been withdrawn.
4. **Grievance redressal** — raise a complaint with the operator using the contact details in Section 1.

To exercise any of these rights, contact us at the email address in Section 1. We will respond within a reasonable period.

> **⚠ LEGAL FLAG:** The DPDP Act provides for the right to **nominate** another person to exercise these rights in the event of death or incapacity. The mechanism for handling such nominations should be addressed before public launch.

---

## 9. Security safeguards

- Passwords are hashed using bcrypt before storage; plaintext passwords are never stored.
- Authentication uses JSON Web Tokens (JWT) with an expiry of 8 hours.
- All API endpoints require a valid Bearer token.
- Per-user data isolation ensures that one account cannot access another's data.
- The application should be deployed over HTTPS in production (operator responsibility at the infrastructure layer).

> **⚠ OPERATIONAL FLAG:** SQLite is currently the default database. It is not suitable for production deployments with multiple concurrent users. PostgreSQL migration is implemented; verify that production deployments use PostgreSQL before opening to additional users.

---

## 10. Cookies and local storage

This application does **not** use cookies to track users.

Streamlit's session mechanics use server-side session state for the duration of your browser tab session. No persistent cookies are written to your browser. Streamlit telemetry has been disabled (`gatherUsageStats = false`).

The JWT access token issued at login is held in Streamlit's in-memory session state and is discarded when the browser tab is closed.

See the [Cookie Policy](#) for a full disclosure.

---

## 11. Children's data

This Service is not intended for use by anyone under the age of 18. We do not knowingly collect personal data from minors. If you become aware that a minor has provided data, please contact us immediately.

---

## 12. Changes to this policy

We will notify users of material changes to this policy by updating the "last updated" date at the top of this document. Continued use of the Service after changes constitutes acceptance.

---

## 13. Contact and grievance officer

For privacy-related queries or to exercise your DPDP Act rights, contact:

**[OPERATOR NAME]**  
**[BUSINESS ADDRESS]**  
**[CONTACT EMAIL]**

> **[PLACEHOLDER — fill in before publication]**

---

*This policy is effective from the date first published. It is a draft pending legal review.*
