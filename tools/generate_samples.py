"""
Regenerate the English sample JDs and fictional sample resumes (PDF) with reportlab.

    pip install reportlab
    python tools/generate_samples.py

Every person, employer, school and contact detail below is invented. Emails use the
reserved example.com domain and phone numbers use the 555 range.
"""

import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPANY = "Acme Technology Ltd."

styles = getSampleStyleSheet()
TITLE = ParagraphStyle("t", parent=styles["Title"], fontSize=20, spaceAfter=4, textColor=colors.HexColor("#154360"))
H = ParagraphStyle("h", parent=styles["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#154360"))
P = ParagraphStyle("p", parent=styles["BodyText"], fontSize=10, leading=14)
SUB = ParagraphStyle("s", parent=P, textColor=colors.HexColor("#555555"))


def bullets(items):
    return ListFlowable(
        [ListItem(Paragraph(i, P), leftIndent=12) for i in items], bulletType="bullet", start="-", leftIndent=12
    )


def build(path, story):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    SimpleDocTemplate(path, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm).build(story)
    print("wrote", os.path.relpath(path, ROOT))


# ---------------------------------------------------------------- JDs
def jd_backend():
    s = [
        Paragraph("Job Description: Python Backend Engineer", TITLE),
        Paragraph(f"Company: {COMPANY} (fictional demo company)", P),
        Paragraph("Location: Xinyi District, Taipei (near City Hall MRT)", P),
        Paragraph("Type: Full-time", P),
        Paragraph("Salary: NT$60,000 - NT$70,000 per month (adjusted for experience)", P),
        Paragraph("About the company", H),
        Paragraph(
            "We help traditional businesses move to high-performance technical architectures through data-driven "
            "and automated workflows. Current flagship projects include an automated recruiting decision system and "
            "an enterprise resource planning (ERP) integration. We are looking for teammates who care about clean "
            "code and sound data-structure design.",
            P,
        ),
        Paragraph("Responsibilities", H),
        bullets(
            [
                "API design and development: build fast, stable, scalable RESTful APIs with Python (Flask / FastAPI / Django).",
                "Database management: design and tune database schemas (MySQL / PostgreSQL) for efficient data access.",
                "Workflow automation: write scripts for data processing, file reading (e.g. PDF parsing) and cross-system data sync.",
                "Cloud integration: connect third-party service APIs (Google Cloud API, Gemini API, Slack, etc.).",
                "Teamwork: ship features with frontend engineers and product managers in an agile process.",
            ]
        ),
        Paragraph("Requirements", H),
        bullets(
            [
                "Education: bachelor's degree or above in computer science, information management or statistics.",
                "Language: strong Python skills.",
                "Experience: 2+ years of software development work or internship experience.",
                "Database: fluent in SQL, with complex-query optimization and data-cleaning skills.",
                "Version control: Git experience with team collaboration (GitHub / GitLab).",
                "English: able to read English technical documentation (TOEIC 750+ or equivalent).",
            ]
        ),
        Paragraph("Nice to have", H),
        bullets(
            [
                "Big-data processing or data visualization tools (Tableau, Power BI).",
                "Deployment and operations experience on a cloud platform (AWS / GCP / Azure).",
                "Familiarity with unit testing and automated test frameworks.",
                "Experience writing technical documentation for large projects.",
            ]
        ),
    ]
    build(os.path.join(ROOT, "sample_jd", "python_backend_engineer_jd.pdf"), s)


def jd_admin():
    s = [
        Paragraph("Job Description: Administrative Assistant", TITLE),
        Paragraph(f"Company: {COMPANY} (fictional demo company)", P),
        Paragraph("Location: Xinyi District, Taipei (near City Hall MRT)", P),
        Paragraph("Type: Full-time", P),
        Paragraph("Salary: NT$30,000 - NT$35,000 per month (adjusted for performance)", P),
        Paragraph("About the company", H),
        Paragraph(
            "We help traditional businesses move to high-performance technical architectures through data-driven "
            "and automated workflows. Current flagship projects include an automated recruiting decision system and "
            "an ERP integration. We are looking for a careful, communicative teammate who enjoys helping the team "
            "with day-to-day operations.",
            P,
        ),
        Paragraph("Responsibilities", H),
        bullets(
            [
                "Office administration: filing, stationery purchasing, mail handling and keeping the workspace tidy.",
                "Document handling: draft memos and reports in Word and organize basic data in Excel.",
                "Recruiting support: organize resumes and send interview notices or thank-you emails as directed.",
                "Cross-team communication: act as a bridge between departments; help schedule meetings and book rooms.",
                "Project administration: take meeting notes and keep project trackers up to date.",
            ]
        ),
        Paragraph("Requirements", H),
        bullets(
            [
                "Education: any major; high school diploma or above.",
                "Software: proficient with Microsoft Office (Word, Excel, PowerPoint).",
                "Personality: careful, responsible, good at communication, coordination and teamwork.",
                "Experience: none required (new graduates welcome; part-time work experience is a plus).",
                "Language: basic English reading ability (able to understand routine emails and documents).",
            ]
        ),
        Paragraph("Nice to have", H),
        bullets(
            [
                "Basic Google Workspace experience (Docs, Sheets, Calendar).",
                "Interest in office automation or data analysis and a willingness to learn new tools.",
                "Basic English listening, speaking, reading and writing (TOEIC 600+ or equivalent).",
            ]
        ),
    ]
    build(os.path.join(ROOT, "sample_jd", "administrative_assistant_jd.pdf"), s)


# ---------------------------------------------------------------- Resumes
RESUMES = [
    dict(
        file="mei-ling_wu.pdf",
        name="Mei-Ling Wu",
        phone="555-0111",
        email="meiling.wu.data@example.com",
        loc="Zhonghe District, New Taipei City",
        summary=(
            "Mathematics graduate with strong logic and data-structure thinking. For the past two years I have worked "
            "as a data analysis assistant, using Python for database queries, data cleaning and automated Excel "
            "reporting. I am teaching myself the Django framework and hope to move into backend development."
        ),
        salary="(not provided)",
        jobs=[
            (
                "Data Processing Assistant | Datapath Marketing",
                "May 2024 - Present (~2 yrs) | Taipei",
                [
                    "Used SQL to pull, clean and de-outlier daily production and sales data.",
                    "Wrote Python (Pandas) scripts that read and parse CSV and Excel files, greatly reducing manual scheduling.",
                    "Helped engineers maintain an existing Python data-sync tool.",
                ],
            )
        ],
        edu=["Lakeview University | B.S. in Mathematics | 2020 - 2024"],
        skills=[
            "Programming: Python (proficient), SQL (proficient)",
            "Tools: Pandas, MySQL, Git (basic)",
            "Languages: Mandarin (native), English (good, TOEIC 755)",
        ],
    ),
    dict(
        file="ariel_lin.pdf",
        name="Ariel Lin",
        phone="555-0112",
        email="ariel.lin.backend@example.com",
        loc="Xinyi District, Taipei",
        summary=(
            "Backend engineer with 4 years of experience designing microservices and automated data pipelines in "
            "Python (FastAPI / Django). Led a database migration and complex SQL optimization at a multinational SaaS "
            "company, cutting report generation time by 50%. Extensive third-party API integration and GCP deployment "
            "experience."
        ),
        salary="70,000 (monthly)",
        jobs=[
            (
                "Backend Engineer | CloudNest Digital",
                "Mar 2022 - Present (~4 yrs 2 mos) | Taipei",
                [
                    "Built and maintained the core internal ERP API in Python (FastAPI), handling 300,000+ requests per day.",
                    "Designed the PostgreSQL schema and tuned performance, optimizing multi-table joins and index configuration.",
                    "Wrote scheduled automation scripts that parse PDF vouchers and sync them to the cloud via Google Cloud API and Slack.",
                    "Wrote unit tests that raised code coverage to 85% and maintained high-quality technical documentation.",
                ],
            )
        ],
        edu=["Riverside University | B.B.A. in Information Management | 2018 - 2022"],
        skills=[
            "Programming: Python (expert), SQL (expert)",
            "Frameworks: FastAPI, Django, PostgreSQL, Docker, Git",
            "Cloud: GCP (GCE, Cloud SQL, Cloud Storage)",
            "Languages: Mandarin (native), English (fluent, TOEIC 885)",
        ],
    ),
    dict(
        file="chia-wei_hung.pdf",
        name="Chia-Wei Hung",
        phone="555-0113",
        email="chiawei.hung@example.com",
        loc="Taichung, Taiwan",
        summary=(
            "Junior web engineer with 1+ years of development experience, familiar with Python and MySQL. Worked on "
            "small and mid-sized company websites and internal management systems. Eager to learn and a good team "
            "player; looking to join a larger engineering team and deepen my backend skills."
        ),
        salary="55,000 (monthly)",
        jobs=[
            (
                "Assistant Engineer | Freelance Studio",
                "May 2023 - Present (~1 yr 3 mos) | Taichung",
                [
                    "Helped build feature modules for a small e-commerce platform, writing basic data-processing logic in Python.",
                    "Maintained MySQL databases, including regular backups and simple query optimization.",
                    "Used Git for version control and worked with frontend engineers on release testing.",
                    "Fixed known bugs in the existing system to improve stability.",
                ],
            ),
            (
                "Software Development Intern | Harbor Communications",
                "Jul 2022 - Feb 2023 (8 mos) | Taichung",
                [
                    "Helped senior engineers write unit test scripts.",
                    "Compiled technical development documents and recorded the system change history.",
                    "Assisted with cross-browser compatibility testing.",
                ],
            ),
        ],
        edu=[
            "Pinecrest Institute of Technology | B.S. in Computer Science | 2019 - 2023",
            "GPA: 3.2 / 4.0",
            "Capstone: smart campus queueing system (Python)",
        ],
        skills=[
            "Programming: Python (basic), SQL, HTML/CSS",
            "Databases: MySQL, MariaDB",
            "Tools: Git, VS Code, Trello",
            "Soft skills: collaboration, eagerness to learn, documentation",
            "Languages: Mandarin (native), English (TOEIC 700)",
        ],
    ),
    dict(
        file="peggy_chen.pdf",
        name="Peggy Chen",
        phone="555-0114",
        email="peggy.chen.code@example.com",
        loc="Banqiao District, New Taipei City",
        summary=(
            "Python web engineer with 3 years of hands-on experience. Familiar with Flask and Django and able to design "
            "and build RESTful APIs independently. Mainly responsible for integrating and maintaining enterprise "
            "administration systems with MySQL, with solid code logic and troubleshooting skills."
        ),
        salary="62,000",
        jobs=[
            (
                "Software Engineer | Ideaworks Software",
                "Apr 2023 - Present (~3 yrs 1 mo) | Taipei",
                [
                    "Built the API for an internal approval system with Python (Flask), owning the backend logic.",
                    "Designed and optimized MySQL schemas and wrote complex SQL for data cleaning and routine maintenance.",
                    "Integrated a third-party e-invoice API and an automated email notification system.",
                    "Used Git for team version control and collaborated in an agile process.",
                ],
            )
        ],
        edu=["Southgate University | B.S. in Statistics | 2016 - 2020"],
        skills=[
            "Programming: Python (proficient), SQL (proficient)",
            "Frameworks: Flask, Django, MySQL, Git, GitHub",
            "Languages: Mandarin (native), English (good, TOEIC 780)",
        ],
    ),
    dict(
        file="mei-ling_chen.pdf",
        name="Mei-Ling Chen",
        phone="555-0115",
        email="meiling.chen@example.com",
        loc="Taipei, Taiwan",
        summary=(
            "4+ years of luxury retail and customer service experience, strong in brand presentation and high-end "
            "client relationship management, and repeatedly helped my store hit its sales targets. Currently looking "
            "for a career change and hoping to bring a service-industry eye for detail into the technology sector."
        ),
        salary="42,000 (monthly)",
        jobs=[
            (
                "Senior Sales Associate | Atelier Row (designer boutique)",
                "Jan 2022 - Present (~4 yrs 4 mos) | Taipei",
                [
                    "Sold and supported high-end designer brands, reaching 110% of the annual sales target.",
                    "Maintained the VIP client list with regular follow-ups and recommendations, lifting average order value by 15%.",
                    "Helped the store manager with inventory management and daily account reconciliation.",
                    "Served as an internal trainer on customer communication and complaint handling.",
                ],
            ),
            (
                "Front Desk Administrator | Brightpath Tutoring Centers",
                "Aug 2020 - Dec 2021 (1 yr 4 mos) | Taipei",
                [
                    "Greeted parents, answered course inquiries and referred them to enrollment.",
                    "Managed student attendance records and handled routine copying, scanning and filing.",
                    "Helped keep the office tidy and purchased stationery supplies.",
                ],
            ),
        ],
        edu=[
            "Harborview Arts University | B.A. in Craft and Design | 2016 - 2020",
            "Honorable mention, campus art exhibition (craft category)",
            "Club: President, Coffee Studies Club",
        ],
        skills=[
            "Design tools: Adobe Photoshop (basic), Illustrator (basic)",
            "Software: Microsoft Word, Excel, PowerPoint",
            "Soft skills: retail sales, customer relationship management (CRM), cross-team communication, event planning",
            "Languages: Mandarin (native), English (fair, TOEIC 550), Japanese (JLPT N3)",
        ],
    ),
    dict(
        file="kevin_huang.pdf",
        name="Kevin Huang",
        phone="555-0116",
        email="kevin.huang.dev@example.com",
        loc="Xinyi District, Taipei",
        summary=(
            "Backend engineer with 2 years of experience focused on Python (Django / FastAPI) development and RESTful "
            "API design. Good grasp of database normalization and SQL performance tuning; contributed to enterprise "
            "ERP modules. Writes clean, maintainable code."
        ),
        salary="65,000",
        jobs=[
            (
                "Backend Engineer | Vanguard Information Systems",
                "May 2024 - Present (~2 yrs) | Taipei",
                [
                    "Built enterprise ERP features with Django, designing efficient data-access APIs.",
                    "Wrote Python scripts integrating Slack webhooks and third-party APIs for real-time system monitoring alerts.",
                    "Optimized MySQL queries; rewriting joins cut one report's generation time by 30%.",
                    "Maintained internal technical documentation and managed code branches with Git.",
                ],
            )
        ],
        edu=["Eastbank University | B.B.A. in Information Management | 2020 - 2024"],
        skills=[
            "Programming: Python (proficient), SQL (proficient)",
            "Frameworks: Django, FastAPI, MySQL, Git, Docker",
            "Languages: Mandarin (native), English (good, TOEIC 765)",
        ],
    ),
]


def resume(r):
    s = [
        Paragraph(r["name"], TITLE),
        Paragraph(f"Phone: {r['phone']} &nbsp;|&nbsp; Email: {r['email']} &nbsp;|&nbsp; Location: {r['loc']}", SUB),
        Paragraph("Summary", H),
        Paragraph(r["summary"], P),
        Paragraph("Expected Salary", H),
        Paragraph(r["salary"], P),
        Paragraph("Work Experience", H),
    ]
    for title, meta, pts in r["jobs"]:
        s += [Paragraph(f"<b>{title}</b>", P), Paragraph(meta, SUB), bullets(pts), Spacer(1, 4)]
    s += [Paragraph("Education", H), bullets(r["edu"]), Paragraph("Skills", H), bullets(r["skills"])]
    build(os.path.join(ROOT, "sample_resumes", r["file"]), s)


if __name__ == "__main__":
    jd_backend()
    jd_admin()
    for r in RESUMES:
        resume(r)
