import setuptools

with open("README.md") as fh:
    long_description = fh.read()

setuptools.setup(
    name="markusmoss",
    version="0.0.1",
    author="Misha Schwartz",
    author_email="mschwa@cs.toronto.edu",
    description="run moss plagiarism detector on MarkUs submissions",
    long_description=long_description,
    long_description_content_type="text/markdown",
    include_package_data=True,
    url="https://github.com/MarkUsProject/markus-moss",
    packages=setuptools.find_packages(),
    install_requires=["mosspy==1.0.8", "toml==0.10.2",
                      "html5lib==1.1", "pypdf",
                      "markusapi>=0.3.0",
                      "requests>=2.32.4",
                      "urllib3>=2.5.0",
                      "toc_pdf_merge @ git+https://github.com/sophia-huynh/toc-pdf-merge.git",
                      ],
    tests_require=["pytest==5.3.1"],
    setup_requires=["pytest-runner"],
    entry_points={"console_scripts": "markusmoss=markusmoss.cli:cli"},
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
