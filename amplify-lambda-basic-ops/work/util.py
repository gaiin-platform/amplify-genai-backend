import re


def extract_sections(prefixes, text):

    # Combine prefixes into a single regex pattern, escaping special characters
    pattern = '|'.join(re.escape(prefix) for prefix in prefixes)

    # Split text based on the pattern
    sections = re.split(f'({pattern})', text)

    # If no sections, return one item with the entire text
    if len(sections) < 2:
        return [{"key": "Content:", "value": sections[0].strip()}]

    # Compile the list of dictionaries
    section_list = []
    for i in range(1, len(sections), 2):
        section_list.append({"key": sections[i], "value": sections[i+1].strip()})

    return section_list


# Test the function
prefixes = ["Thoughts:", "Contents:", "Follow-ups:"]

text = """
Someo other stuff

Thought: I'll develop comprehensive content on Vanderbilt University by expanding on multiple aspects of the institution, including its foundation, academic programs, research contributions, notable alumni, campus life, and more. Here's the extended content in eight paragraphs:

Content: Vanderbilt University, commonly referred to as Vanderbilt, is a private research university located in Nashville, Tennessee. It was founded in 1873 with a $1 million donation from Cornelius Vanderbilt, a shipping and rail magnate, despite having never been to the South himself. Vanderbilt's vision was to strengthen the ties between Northern and Southern states following the Civil War. The university was initially affiliated with the Methodist Episcopal Church, South, until it gained independence in 1914.

Over the years, Vanderbilt has grown significantly in both stature and size. Today, it comprises ten schools and colleges offering undergraduate, graduate, and professional degrees in fields ranging from humanities to engineering. The diverse array of academic programs is designed to encourage interdisciplinary collaboration and promote innovation. Vanderbilt is particularly renowned for its medical, law, and business schools, which consistently rank among the top in national and international rankings.

A key aspect of Vanderbilt's offerings is its commitment to cutting-edge research. The university is home to several research centers and institutes, such as the Vanderbilt-Ingram Cancer Center and the Institute for Space and Defense Electronics. These centers facilitate advanced research in various fields, contributing to significant scientific and technological advancements. Through partnerships with government agencies and private enterprises, Vanderbilt researchers tackle complex global challenges.

The influence of Vanderbilt University extends beyond its academic and research contributions. It boasts a long list of notable alumni who have made remarkable strides in diverse domains. From former Vice President of the United States Al Gore to celebrated author James Patterson, Vanderbilt graduates have left an indelible mark on society. These distinguished alumni reflect the university's emphasis on developing leaders and innovators who drive positive change.

Campus life at Vanderbilt is vibrant and dynamic, offering a supportive environment that fosters personal and professional growth. The university provides numerous extracurricular activities, including over 500 student organizations, Greek life, and community service initiatives. The on-campus housing system, particularly the residential colleges, enhances the sense of community and encourages meaningful interactions among students.

One of the cornerstones of Vanderbilt's student experience is its commitment to inclusivity and diversity. The university actively promotes a culture of respect and understanding, with initiatives aimed at supporting underrepresented groups and fostering an inclusive campus environment. Programs such as MOSAIC, which targets multicultural recruitment, and the Office of LGBTQI Life are just a few examples of Vanderbilt's dedication to inclusivity.

Athletics also play a significant role at Vanderbilt, with the university participating in NCAA Division I as part of the Southeastern Conference (SEC). The Commodores, as the university's sports teams are known, compete in a variety of sports, including football, basketball, and baseball. Vanderbilt's athletic facilities are state-of-the-art, and the university places a strong emphasis on balancing academic and athletic excellence.

Looking towards the future, Vanderbilt continues to build on its legacy of excellence and innovation. The university's strategic plans aim to expand its global reach, invest in groundbreaking research, and enhance the student experience. Through these efforts, Vanderbilt strives to remain at the forefront of higher education, shaping the next generation of leaders and making a profound impact on the world.

Follow-up: Would you like additional details on any specific sections, or should we move on to compile the report with the content above? If specific citations are needed, please provide the sources or indicate the kind of sources to include. Thought: I'll add another section focusing on Vanderbilt University's community engagement and contributions to society, emphasizing its role in public service and outreach programs.

Content: Beyond its academic and research achievements, Vanderbilt University is deeply committed to community engagement and societal contributions. The university actively participates in various outreach programs designed to support local, national, and global communities. For instance, the Vanderbilt Kennedy Center offers services and programs aimed at improving the lives of individuals with developmental disabilities and their families. Additionally, the Turner Family Center for Social Ventures empowers students to leverage business principles to address social and environmental issues.

Vanderbilt's impact on the community is further amplified through its healthcare facilities. The Vanderbilt University Medical Center (VUMC) is not only a leader in medical research and education but also a critical provider of healthcare services in the region. VUMC's commitment to patient care and community health initiatives underscores the university's dedication to public service. The university also runs numerous health outreach programs, such as free clinics and health education workshops, to benefit underserved populations.

The university's involvement in community service is also reflected in its student body. Vanderbilt students engage in various volunteer activities, from tutoring local schoolchildren to participating in Habitat for Humanity projects. The Office of Active Citizenship and Service (OACS) offers programs that encourage students to be civically engaged and develop a sense of responsibility toward society. These initiatives not only provide valuable support to the community but also help students cultivate skills and values that are essential for their future careers and personal growth.

Follow-up: Would you like to expand further on any of these aspects or add another distinct section to the report? Please provide guidance on the next steps or any additional information you would like to include.
"""


for section in extract_sections(prefixes, text):
    print("Key:", section["key"])
    print("Value:", section["value"])
