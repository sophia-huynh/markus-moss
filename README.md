## MarkUs Moss

Tool to generate [Moss](http://moss.stanford.edu/) reports from [MarkUs](https://github.com/MarkUsProject/Markus) submissions.

### Installation

```shell script
pip install git+https://github.com/MarkUsProject/markus-moss.git
```

Optional External Dependencies:

- [pandoc](https://pandoc.org/) (required for `copy_files_to_pdf` action)

### Usage

```shell script
markusmoss {arguments}
```

Running `markusmoss` is guaranteed to be idempotent as long as the `--force` option is not used.

#### arguments:

MarkUs moss takes several optional command line arguments. Depending on the action selected, different arguments
are required. If a required argument is not specified, markusmoss.py will raise an error during runtime. 

Arguments can be specified on the command line or in a config file (see below).

* --help : show help message and exit
* --markus-api-key : (string) markus api key
* --markus-url : (string) markus url
* --markus-course : (string) markus course short identifier
* --markus-assignment : (string) markus assignment short identifier
* --moss-userid : (integer) moss userid
* --moss-report-url : (string) moss report url
* --config : (string) config file (see format below) : default is `${PWD}/.markusmossrc`
* --workdir : (string) working directory : default is `${PWD}`
* --actions : (strings) actions (see below) : default is to run all actions in order 
* --groups : (strings) only use the groups listed : default is to use all groups from the specified MarkUs assignment
* --language : (strings) moss programming languages (see below)
* --file-glob : (string) glob describing submission files to test with moss (see below): default is '\*\*/\*'
* --generate-config: (string) write a config file (see format below) to the path specified from all other arguments given.
                              If no path is given to this flag, write to stdout. 
* --html-parser : (string) bs4 html parser: default is 'html.parser'
* --force : redo all specified actions: default is not to redo previously executed actions
* --verbose : log actions to stdout
* --selected-groups : (int) a single case number or (string) 2 or more group names to generate individual reports for.

#### config format

Instead of passing arguments to the command line, most arguments can be specified in a 
[toml](https://github.com/toml-lang/toml) configuration file. All command line arguments can also be specified in the 
config file except:

* config
* help

It is recommended to store options in the config file that are unlikely to change (ex: markus-api-key, markus-url, moss-userid)
and to pass other options to the command line.

An example config file might contain the following:

```toml
markus_api_key="abc123xyz"
markus_url="http://example.com/markus"
moss_userid=123456789
```

Note that the config file uses underscores instead of hyphens.

Information about obtaining the `markus_api_key` and `markus_url` can be found here: https://github.com/MarkUsProject/Markus/wiki/RESTful-API#authentication

Information about obtaining the `moss_userid` can be found here: http://moss.stanford.edu/

##### Selecting cases
When using the `--selected-groups` argument, the `selected` directory will be generated to produce a folder
with the specified case (in the case of a case number being provided), or all cases involving the
groups (in the case of a list of group names being provided).

The toml file can take multiple sets of groups through the `selected_groups` argument. For example:
```toml
selected_groups = [["group 1", "group 2"],
                   ["group 3", "group 4", "group 5"]]
```

When more than 2 groups are provided in a single set, all cases involving any pair of groups
will be reported.

##### Excluding matches
Specific matches within cases may also be provided through the `exclude_matches` argument in the `toml`
config file to exclude them from selected results (see above). 

This should map the case number to the match numbers to exclude. For example:

```toml
exclude_matches = { 1 = [0, 8] }
```
This would exclude matches 0 and 8 from being reported for reports containing case 1.

#### actions

* download_submission_files
    * Download submission files from MarkUs and write them to the `submission_files` subdirectory
    * required arguments:
        * workdir
        * markus-api-key
        * markus-url
        * markus-course
        * markus-assignment
* copy_files_to_pdf
    * Copy the files in the `submission_files` subdirectory to the `pdf_submission_files` subdirectory
    * depends on:
        * download_submission_files
    * required arguments:
        * workdir
        * file-glob
    * require extenal dependencies:
        * [pandoc](https://pandoc.org/)
* download_starter_files
    * Download the starter file (if any) from MarkUs and write them to the `starter_files` subdirectory
    * required arguments:
        * workdir
        * markus-api-key
        * markus-url
        * markus-course
        * markus-assignment
* run_moss
    * Run moss checker on submission files and write url of moss report to `moss_report/report_url.txt`
    * depends on:
        * download_submission_files
        * download_starter_files
    * required arguments:
        * workdir
        * moss-userid
        * file-glob
* download_moss_report
    * Download full moss report from moss report url to `moss_report/report` subdirectory
    * depends on:
        * run_moss
    * required arguments:
        * workdir
        * markus-assignment
* write_final_report
    * Compile moss report (see report format below) to `final_report`
    * depends on:
        * copy_files_to_pdf
        * download_moss_report
    * required arguments:
        * workdir
        * file-glob
        * html-parser

All subirectories are assumed to be in the specified `workdir`.

#### moss languages

For a full list of programming languages that can be parsed by moss:

http://moss.stanford.edu/

#### file-glob explanation

MarkUs submissions often contain multiple file types but moss can only evaluate similarity for one language at a time.
In order to specify which files should be sent to moss for evaluation, the file-glob argument can be used.

For example, to run moss on python files in any subdirectory from a MarkUs submission:

```shell script
markusmoss --config my_config.toml --language python --file-glob '**/*.py'
```

### Output Files

After running all actions in order, the contents of the `workdir` should look like:

```
workdir/
├── final_report/
│   └── assignment_short_id/
│       ├── case_1/
│       │   ├── group_1
│       │   │   ├── group_data.csv
│       │   │   ├── org
│       │   │   │   └── source_file.py
│       │   │   └── pdf
│       │   │       └── source_file.py.pdf
│       │   ├── group_2
│       │   │   ├── group_data.csv
│       │   │   ├── org
│       │   │   │   └── source_file.py
│       │   │   └── pdf
│       │   │       └── source_file.py.pdf
│       │   └── moss.html
│       ├── ...
│       ├── case_n/
│       └── case_overview.csv
├── moss_report/
│   ├── report/
│   └── report_url.txt
├── pdf_submission_files/
│   ├── group_1/
│   ├── ...
│   └── group_n/
├── starter_files/
│   └── assignment_short_id/
└── submission_files/
    ├── group_1/
    ├── ...
    └── group_n/
```

The all directories except for those contained in `final_report` are not required for the final report and can be
deleted safely once all actions have been run.

#### final report format

The final report contains a subdirectory for the MarkUs assignment. 
Within that directory is a case overview file with the details of each case reported by moss.
There is a subdirectory for each case which contains the submission files for each group in both text and pdf formats.
Each case also contains one group_data.csv file per group which contains information about each group.
Each case also contains a moss.html file which contains the moss report for the specific case in question. 
This report can be viewed by opening the moss.html file in any web browser.

##### group_data.csv format

This csv file contains information about the group members, it contains the following columns:

- group_name : group name on MarkUs
- user_name : user name on MarkUs
- first_name : the student's first name
- last_name : the student's last name
- email : the student's email address (if available)
- id_number : the sutdent's id number (if available)

##### case_overview.csv format

This csv file contains information about the cases reported by moss, it contains the following columns:

- case : the case name
- groups : the group names of all groups compared for the given case (separated by `;`)
- similarity (%) : the percentage similarity score reported by moss
- matched_lines : the number of identical lines of code reported by moss 

