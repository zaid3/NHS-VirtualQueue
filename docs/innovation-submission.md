# NHS Innovation Service submission brief

**Innovation:** NHS VirtualQueue  
**Creator and maintainer:** A Haque (zaid)  
**Repository:** https://github.com/zaid3/NHS-VirtualQueue  
**Prepared:** 28 October 2026; licensing model updated 29 October 2026

**Status:** Functional software with synthetic technical validation; not connected to live NHS services and not clinically approved.

## Short description

NHS VirtualQueue is an independent application for clinically supervised remote waiting and coordinated hospital arrivals. It is intended to work alongside existing NHS clinical and booking systems. After a clinician assesses a patient and the receiving service confirms an arrival window, the patient can see their instructions, visit status and journey plan. Staff can follow up when a review is overdue, a message fails, capacity changes or symptoms worsen.

## The problem

Some patients who could safely wait away from hospital spend long periods in a waiting room with little visibility of their next step. Simply moving a queue online can create additional risks: the wrong record may be linked, a patient may misunderstand an estimated time, an unconfirmed booking may appear valid, or staff may assume a message was received.

The project addresses coordination and visibility within a clinically approved pathway. It does not claim that every A&E patient can wait at home, that all attendances involve a four- or five-hour wait, or that software alone can remove capacity pressures.

## The approach and its proposed value

The central design is one accountable waiting episode that links clinician approval, receiving-service confirmation, current capacity, travel constraints and patient follow-up. DOB, family name and postcode support record tracing, with authorised verification before a link is recorded. Identity matching is kept separate from telephone verification.

An arrival window remains a clinical plan rather than a guarantee of treatment. Uncertainty is visible: an unanswered booking or failed message produces an explicit follow-up state. Clinical priority remains with the care team. A cancellation cannot silently discharge someone who has since reported worsening symptoms. The activity history records decisions and corrections without erasing earlier actions.

These are specific design choices for evaluation. Existing NHS 111 booking pathways, urgent care self-service products and local waiting-at-home services mean the general concept is not claimed as a first invention. The proposed contribution is the integrated coordination workflow, transparent uncertainty handling and inspectable implementation. Its relative value needs testing with NHS users and existing products.

## Intended users and integration

Patients selected by a clinician for an approved urgent care pathway, receiving-service clinicians and coordinators, and operational teams responsible for callbacks and arrivals. The proposed first evaluation is a tightly scoped adult urgent treatment pathway, subject to clinical partner agreement.

The application is designed around a trust-owned integration gateway to existing EPR/PAS and booking systems. Included PDS and BaRS transport clients establish technical boundaries, but there is no live API access, named EPR adapter or completed NHS conformance assessment. Generic OIDC staff authentication is implemented; NHS login/CIS2 integration remains deployment-specific work.

People who cannot use the digital route must retain established telephone and in-person access. Proxy access, paediatric pathways, safeguarding scenarios, unstable housing/address information and communication needs require specific design with the partner service.

## Evidence available now

The repository contains working API and browser interfaces, database migrations, automated workflow/security tests, mock integration tests, a hazard register, deployment instructions and explicit integration contracts. The [validation record](validation.md) identifies actual tests and their limits. All test records are synthetic.

There are no patient outcome results, NHS pilots, customer deployments, independent penetration-test results or proven reductions in waiting time or cost. Technical tests show behaviour under selected scenarios; they are not clinical effectiveness evidence.

## Outcomes proposed for evaluation

Measure total time from first contact to treatment and time physically waiting on site separately. Reducing time in a waiting room must not conceal a longer total delay. Also measure missed attendance, overdue assessment, deterioration/escalation events, successful patient contact, booking discrepancies, patient understanding, accessibility and staff workload.

Agree baseline, inclusion/exclusion criteria, sample size, monitoring, escalation and stopping rules with clinical and evaluation partners before recruiting patients. Any target improvement should be stated as a prospective hypothesis until measured. No percentage benefit is claimed in this submission.

## Support requested

Help identifying an appropriate NHS clinical and operational partner; assessing pathway fit against existing services; designing a safe integration and supervised evaluation; developing clinical safety and DTAC evidence; determining relevant regulatory requirements; and planning procurement and adoption if evidence supports them.

## Submission preparation

The proposed revenue model is paid licences for new proprietary material and separately agreed hosting, integration, implementation, maintenance and support. Prices, service commitments and adoption costs have not been set. Material already released under Apache 2.0 retains its existing rights; a licensing change cannot require payment for that earlier application. See [commercial licensing](../COMMERCIAL.md). The new material should be described as source-available, with no claim that it has an open source licence.

Use the account's correct organisation, contact and business details when completing the Innovation Service record. Do not put private account information in this public repository. Attach technical evidence without patient data and state its development stage accurately. Submission is for needs assessment and referral to support organisations; it is not NHS endorsement or permission to treat patients.

This brief is prepared for the owner to review and submit. No Innovation Service account submission has been made by this repository change.

## References

* [NHS Innovation Service submission guidance](https://innovation.nhs.uk/training-and-resources/innovator-guidance/submitting-your-innovation-record/)
* [Uploading evidence](https://innovation.nhs.uk/training-and-resources/innovator-guidance/upload-evidence-in-innovation-record/)
* [Existing urgent care self-service product](https://digital.nhs.uk/services/urgent-care-self-service-product)
* [Example waiting-at-home pathway](https://www.kingstonandrichmond.nhs.uk/patients-and-families/patient-leaflets/utc-waiting-at-home)
* [NHS integration sources](nhs-integration.md#sources) and [clinical safety sources](clinical-safety.md#sources)
