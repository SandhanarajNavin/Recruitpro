"""Seed data for local development and tests.

One job description and ten candidate resumes, carried over from the prototype so
the repository has a realistic pool the moment it is created. The spread is
deliberate: strong fits, adjacent-domain fits, a junior, a manager who has stopped
shipping, and two clear mismatches — a pool where every candidate scores well
tests nothing.
"""

from __future__ import annotations

SAMPLE_JOB_DESCRIPTION = "Senior Full-Stack Engineer, Payments Platform\n\nAbout the role\nWe are building the ledger and checkout surface that moves roughly $4B a year for\nmid-market merchants. You will own end-to-end features across a Next.js/TypeScript\nfrontend and a Node.js service layer backed by PostgreSQL, and you will be on the hook\nfor the reliability of what you ship.\n\nWhat you will do\n- Design and ship payment flows end to end, from React UI through to the ledger writes\n- Own service reliability: instrumentation, alerting and on-call for your services\n- Lead the migration of our checkout services onto Kubernetes on AWS\n- Mentor two mid-level engineers and raise the bar in code review\n\nRequirements\n- 6+ years of professional software engineering experience\n- Deep TypeScript, including a production Next.js or React application at scale\n- Strong Node.js service work and hands-on PostgreSQL data modelling\n- Production experience with AWS and Docker; Kubernetes exposure is essential\n- Experience with payments, fintech or another domain with hard correctness requirements\n- Track record of owning services in production, including CI/CD and observability\n\nNice to have\n- Go, Kafka, Terraform\n- Experience with PCI-scoped systems\n- Prior mentoring or tech-lead experience"

#: (external_id, full_name, resume_text)
SAMPLE_CANDIDATES: list[tuple[str, str, str]] = [
    (
        "c1",
        "Priya Raghunathan",
        "Senior Software Engineer, Payments\n9 years professional experience\n\nNorthwind Payments - Senior Software Engineer (2020 - Present)\n- Owned the merchant checkout surface: Next.js + TypeScript, 40M sessions/month\n- Rebuilt the double-entry ledger writer in Node.js and PostgreSQL, cut settlement\n  discrepancies by 92%\n- Led the migration of 14 checkout services from EC2 to Kubernetes on AWS, reduced\n  deploy time from 45 minutes to 6\n- Set up Datadog SLOs and on-call rotation for the payments group\n- Mentored 3 mid-level engineers, two promoted to senior\n\nKestrel Bank - Software Engineer (2017 - 2020)\n- Built PCI-scoped card tokenisation service in Node.js, PostgreSQL, Terraform\n- Introduced GitHub Actions CI/CD across 9 repositories\n\nB.Tech Computer Science, NIT Trichy\nAWS Certified Solutions Architect - Associate\nSkills: TypeScript, React, Next.js, Node.js, PostgreSQL, AWS, Docker, Kubernetes,\nTerraform, Kafka, CI/CD, Observability, System Design",
    ),
    (
        "c2",
        "Daniel Okafor",
        "Staff Engineer, Infrastructure\n12 years professional experience\n\nVega Logistics - Staff Engineer (2019 - Present)\n- Ran the platform group: Kubernetes on AWS across 300+ services, 99.98% availability\n- Built the internal deploy tooling in Go, adopted by 120 engineers\n- Owned Prometheus and Grafana observability stack, cut MTTR by 60%\n- Designed the Kafka event backbone processing 2 billion events/day\n\nHelios Cloud - Senior Site Reliability Engineer (2014 - 2019)\n- Terraform and Ansible automation for a 4000-node fleet\n- Node.js internal APIs, PostgreSQL operational tooling\n\nM.Sc Distributed Systems, University of Edinburgh\nCertified Kubernetes Administrator (CKA)\nSkills: Go, Kubernetes, AWS, Terraform, Kafka, Prometheus, Grafana, Node.js,\nPostgreSQL, Docker, Linux, CI/CD, Distributed Systems",
    ),
    (
        "c3",
        "Mei-Lin Chow",
        "Senior Frontend Engineer\n8 years professional experience\n\nArcadia Retail - Senior Frontend Engineer (2019 - Present)\n- Led the Next.js and TypeScript rewrite of the e-commerce storefront, improved LCP\n  from 4.1s to 1.3s and lifted conversion 11%\n- Built the design system in React and Tailwind CSS, adopted across 6 product teams\n- Drove accessibility to WCAG 2.1 AA across the checkout funnel\n- Playwright and Jest test suites, GitHub Actions CI\n\nLumen Studio - Frontend Developer (2016 - 2019)\n- React and Redux applications for B2C marketplace clients\n- Figma-to-component workflow, WebSockets live pricing widgets\n\nB.Sc Interaction Design, Monash University\nSkills: TypeScript, JavaScript, React, Next.js, Redux, Tailwind CSS, Accessibility,\nPlaywright, Jest, Figma, CI/CD, Performance Optimization",
    ),
    (
        "c4",
        "Tomas Erikson",
        "Backend Engineer, Fintech\n7 years professional experience\n\nSolvent Capital - Backend Engineer (2018 - Present)\n- Built the reconciliation engine in Node.js and PostgreSQL for a payments book of\n  $900M/year, reduced manual reconciliation work 78%\n- Modelled the ledger schema and wrote the migration tooling; zero-downtime cutover\n- Deployed on AWS ECS with Docker, GitHub Actions CI/CD\n- PCI-DSS scoped services, worked directly with auditors\n- Introduced structured logging and Grafana dashboards\n\nFjord Insurance - Junior Developer (2016 - 2018)\n- REST APIs in Express and PostgreSQL for claims processing\n\nB.Sc Computer Engineering, KTH Royal Institute of Technology\nSkills: TypeScript, Node.js, Express, PostgreSQL, AWS, Docker, REST, GraphQL,\nCI/CD, Grafana, Data Modeling, Microservices",
    ),
    (
        "c5",
        "Aisha Bello",
        "Full-Stack Engineer\n6 years professional experience\n\nTandem Health - Full-Stack Engineer (2020 - Present)\n- Shipped patient billing flows end to end: Next.js, TypeScript, Node.js, PostgreSQL\n- Migrated the billing service to Kubernetes on AWS alongside the platform team\n- Built the FHIR claims integration handling 1.2M claims/year\n- On-call for the billing service, wrote the runbooks and Datadog monitors\n- HIPAA-scoped work with hard correctness requirements\n\nCraftline - Software Engineer (2019 - 2020)\n- React and Django internal tooling for a healthtech marketplace\n\nB.Sc Computer Science, University of Lagos\nSkills: TypeScript, React, Next.js, Node.js, PostgreSQL, AWS, Docker, Kubernetes,\nCI/CD, Observability, REST, Python, Django",
    ),
    (
        "c6",
        "Rafael Duarte",
        "Machine Learning Engineer\n9 years professional experience\n\nCortex Labs - Senior Machine Learning Engineer (2019 - Present)\n- Built fraud detection models for a payments processor, cut false positives 34%\n- Productionised PyTorch models behind FastAPI services on Kubernetes and GCP\n- Feature pipelines in Spark, Airflow and Snowflake, 400M events/day\n- MLOps tooling and model monitoring for 22 production models\n\nInstituto Nexo - Data Scientist (2015 - 2019)\n- NLP research, scikit-learn and Pandas pipelines\n\nPh.D Machine Learning, Universidade de São Paulo\nSkills: Python, PyTorch, TensorFlow, Machine Learning, Deep Learning, NLP, MLOps,\nSpark, Airflow, Snowflake, Kubernetes, GCP, FastAPI, SQL",
    ),
    (
        "c7",
        "Grace Lindqvist",
        "Junior Full-Stack Developer\n2 years professional experience\n\nPayflow (Series A) - Junior Full-Stack Developer (2023 - Present)\n- Built merchant dashboard screens in Next.js and TypeScript\n- Node.js API endpoints against PostgreSQL, reviewed by senior engineers\n- Wrote Jest tests, raised coverage on the dashboard from 40% to 78%\n- Shadowed the on-call rotation for the payments API\n\nInternship - Nordic Fintech Hub (2022)\n- React prototypes for open banking demos\n\nB.Sc Software Engineering, Chalmers University of Technology\nSkills: TypeScript, JavaScript, React, Next.js, Node.js, PostgreSQL, Jest, Docker,\nGit, REST",
    ),
    (
        "c8",
        "Hyun-Woo Park",
        "Engineering Manager / former Senior Engineer\n14 years professional experience\n\nMeridian Commerce - Engineering Manager, Checkout (2021 - Present)\n- Manage 11 engineers across two squads owning checkout and payments integrations\n- Set the technical direction for the Kubernetes migration on AWS; hands-off delivery\n- Hiring, performance and roadmap ownership; last hands-on commit in 2021\n\nMeridian Commerce - Senior Software Engineer (2016 - 2021)\n- Built the checkout service in Node.js and PostgreSQL, $1.4B annual volume\n- React and TypeScript checkout UI, cut cart abandonment 9%\n\nOrbis Systems - Software Engineer (2010 - 2016)\n- Java and Spring Boot order management services\n\nB.Sc Computer Science, KAIST\nSkills: Node.js, TypeScript, React, PostgreSQL, AWS, Kubernetes, Java, Spring Boot,\nMicroservices, System Design, Agile",
    ),
    (
        "c9",
        "Nadia Rahman",
        "Senior Software Engineer (contract)\n8 years professional experience\n\nIndependent Contractor (2022 - Present)\n- Next.js and TypeScript rebuild of a B2B insurance quoting portal\n- Node.js services on AWS Lambda and PostgreSQL, Terraform infrastructure\n- Kubernetes work on one engagement: migrated 4 services for a logistics client\n\nQuillon Group - Senior Software Engineer (2018 - 2021)\n- GraphQL API layer in Node.js over PostgreSQL, 3M requests/day\n- Led the CI/CD move to GitHub Actions, cut build times 55%\n\nCareer break 2021 - 2022 (relocation)\n\nB.Sc Computing, University of Dhaka\nSkills: TypeScript, React, Next.js, Node.js, GraphQL, PostgreSQL, AWS, Serverless,\nTerraform, Docker, Kubernetes, CI/CD",
    ),
    (
        "c10",
        "Marcus Feld",
        "Senior Java Engineer\n11 years professional experience\n\nZentrum Bank - Senior Java Engineer (2016 - Present)\n- Core banking payments engine in Java and Spring Boot, SEPA and SWIFT rails\n- Oracle and PostgreSQL data modelling for the transaction ledger\n- Ran the on-premise Jenkins CI/CD pipelines and release process\n- Reduced end-of-day batch runtime from 6 hours to 90 minutes\n\nBauer Software - Java Developer (2013 - 2016)\n- Spring Boot microservices for insurance underwriting\n\nDiplom-Informatiker, TU München\nSkills: Java, Spring Boot, PostgreSQL, SQL, Microservices, Jenkins, CI/CD, Linux,\nKafka, Docker, System Design",
    ),
]
