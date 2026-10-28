# Intended use and clinical safety work

This document supports clinical safety review. It is not a signed clinical safety case, a deployment approval or a certification.

## Intended use

Coordinate callbacks and arrival windows for patients whom an authorised clinician has assessed as suitable for remote waiting within a participating service's approved pathway. Preserve the existing EPR/PAS as the clinical system of record and maintain a traceable operational episode.

The proposed first evaluation is a narrowly scoped adult urgent treatment pathway with a named receiving team, staffed callback service, accessible telephone fallback and explicit exclusions agreed by its clinical lead. The software does not infer eligibility from symptoms. Deployment-specific age, safeguarding, language, capacity and accessibility criteria require design and validation before patient use.

This release does not diagnose, dispatch ambulances, provide autonomous triage, guarantee treatment times or replace initial assessment. The numeric priority scale is an internal field; any mapping to a local triage framework must be clinically approved. Priority 3–5 alone never establishes suitability for remote waiting.

## Safety behaviours

An online request starts a callback episode, not permission to stay at home. Clinical assessment and identity confirmation are explicit clinician assertions. A confirmed hospital booking is required before displaying an arrival window. Unconfirmed or stale information is withheld. Worsening symptoms, expired review, unavailable capacity and uncertain delivery require staff follow-up.

Missed windows and cancellation requests never automatically discharge a person. A confirmed reservation remains held until its cancellation is acknowledged or its care episode is appropriately completed. Corrections add history; there is no button that erases clinical events. Late external events cannot roll back an in-person care state.

Some controls depend on people and external systems: clinical assessment, safe callback staffing, timely care, ambulance pre-alert handling, telephone follow-up, receiving-system confirmation and safeguarding. A running server is not evidence those controls exist.

## Required work before live clinical use

Appoint the manufacturer's clinical safety officer and the adopting service's clinical safety and operational owners. Confirm DCB0129 and DCB0160 applicability and the current requirements with them. Develop the hazard analysis, safety case and safety case report for the actual intended use; review unresolved hazards in [hazards.csv](hazards.csv). Do not mark their residual risk accepted merely because a test passes.

Complete deployment-specific DTAC evidence for clinical safety, data protection, technical security, interoperability, usability and accessibility. NHS England says to use the current DTAC form at the point of assessment; older forms were superseded in 2026. Determine additional regulatory obligations from the intended use rather than asserting that the software is exempt or automatically a medical device.

Conduct patient and staff usability research, an independent accessibility assessment, an independent penetration test, failure drills, a data protection impact assessment and a controlled integration evaluation. Agree a staffed downtime pathway and measurable go/no-go criteria. An environment variable containing a clinical release reference records a deployment decision; it cannot manufacture that decision or validate its authenticity.

## Sources

* [Clinical risk management standards](https://digital.nhs.uk/services/clinical-safety/clinical-risk-management-standards)
* [Current DTAC guidance](https://digital.nhs.uk/services/digital-technology-assessment-criteria-dtac)
* [When to go to A&E](https://www.nhs.uk/nhs-services/urgent-and-emergency-care-services/when-to-go-to-ae/)
* [NHS emergency department initial assessment](https://www.england.nhs.uk/guidance-for-emergency-departments-initial-assessment/)
