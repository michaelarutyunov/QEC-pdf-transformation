### IMPORTS ###

import spacy
from spacy.matcher import Matcher
from spacy.symbols import ORTH
import argparse
import pymupdf
import anthropic
import copy
import json
import os
import re
import uuid
from dotenv import load_dotenv

load_dotenv()

### 1. CONFIGURATION ###

def load_config(config_path="config.json"):
    """
    Load configuration from JSON file and return as a dictionary.
    
    Args:
        config_path (str): Path to the configuration file. Default is "config.json"
        
    Returns:
        dict: Complete configuration dictionary or empty dict if file not found or invalid
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
            return config_data
    except FileNotFoundError:
        print(f"Configuration file {config_path} not found.")
        return {}
    except json.JSONDecodeError:
        print(f"Error decoding JSON from {config_path}.")
        return {}

def get_folder_paths(config_data):
    """Extract folder paths from config dictionary."""
    folder_paths = config_data.get("folder_paths", {})
    return {
        "transcripts_pdf_folder": folder_paths.get("transcripts_pdf_folder", None),
        #"transcripts_cleantxt_folder": folder_paths.get("transcripts_cleantxt_folder", None),
        #"metadata_folder": folder_paths.get("metadata_folder", None),
        #"utterances_folder": folder_paths.get("utterances_folder", None),
        "final_json_folder": folder_paths.get("final_json_folder", None),
    }

def get_test_mode_info(config_data):
    """Extract test mode information from config dictionary."""
    test_mode = config_data.get("test_mode", {})
    return {
        "enabled": test_mode.get("enabled", False),
        "file_name": test_mode.get("file_name", None),
        "debug_mode": test_mode.get("debug_mode", False),
        "diagnostics_folder": test_mode.get("diagnostics_folder", None)
    }

def get_api_setup(config_data):
    """Extract API setup from config dictionary."""
    api_setup = config_data.get("api_setup", {})
    api_key_name = api_setup.get("api_key_name", None)
    model = api_setup.get("model", None)
    input_cost_per_million = api_setup.get("input_cost_per_million", 0)
    output_cost_per_million = api_setup.get("output_cost_per_million", 0)
    
    return api_key_name, model, input_cost_per_million, output_cost_per_million

def get_cleaning_parameters(config_data):
    """Extract cleaning parameters from config dictionary."""
    cleaning_parameters = config_data.get("cleaning_parameters", {})
    return {
        "keep_bold_tags": cleaning_parameters.get("keep_bold_tags", False),
        "keep_italics_tags": cleaning_parameters.get("keep_italics_tags", False),
        "keep_underline_tags": cleaning_parameters.get("keep_underline_tags", False),
        "keep_capitalization_tags": cleaning_parameters.get("keep_capitalization_tags", False)
    }


### 2. PDF IMPORT AND TEXT PRE-PROCESSING ###

# Move punctuation outside of bold tags and adjust colons from "word :" to "word: "
def normalize_punctuation(text):
    """Move punctuation outside of bold tags."""
    # Manage colons
    # text = re.sub(r'(\w):', r'\1 :', text) # add space before colon
    text = re.sub(r':(\w)', r': \1', text)  # add space after colon
    text = re.sub(r'(\w+)\s+:', r'\1:', text)  # remove space before colon

    # Remove isolated punctuation marks like " : "
    text = re.sub(r'\s+([,.;:!?])\s+', ' ', text)
    
    # Strip leading/trailing whitespace
    text = text.strip()
    
    return text

# Optimize TEXT tags (TAG_2, TAG_4, TAG_3)
def optimize_text_tags(text):
    """Optimize text tags by removing unnecessary formatting tags."""

    # Remove TEXT tags with single letter, number or punctuation (from "<TAG> [char] <TAG>" to "<TAG>")
    text = re.sub(r'<TAG_2>\s*[A-Za-z]\s*<TAG_2>', r' <TAG_2> ', text, flags=re.IGNORECASE)
    text = re.sub(r'<TAG_2>\s*(\d+)\s*<TAG_2>', r' <TAG_2>', text, flags=re.IGNORECASE)
    text = re.sub(r'<TAG_2>\s*[,.;:!?-]\s*<TAG_2>', r' <TAG_2>', text, flags=re.IGNORECASE)
    text = re.sub(r'<TAG_3>\s*[A-Za-z]\s*<TAG_3>', r' <TAG_3> ', text, flags=re.IGNORECASE)
    text = re.sub(r'<TAG_3>\s*(\d+)\s*<TAG_3>', r' <TAG_3>', text, flags=re.IGNORECASE)
    text = re.sub(r'<TAG_3>\s*[,.;:!?-]\s*<TAG_3>', r' <TAG_3>', text, flags=re.IGNORECASE)
    text = re.sub(r'<TAG_4>\s*[A-Za-z]\s*<TAG_4>', r' <TAG_4> ', text, flags=re.IGNORECASE)
    text = re.sub(r'<TAG_4>\s*(\d+)\s*<TAG_4>', r' <TAG_4> ', text, flags=re.IGNORECASE)

    # optimize multiple TEXT tags in a row, keep only one
    text = re.sub(r'(<TAG_4>\s*){2,}', r'\1', text)
    text = re.sub(r'(<TAG_3>\s*){2,}', r'\1', text)
    text = re.sub(r'(<TAG_2>\s*){2,}', r'\1', text)

    # Ensure single space between tags
    text = re.sub(r'>\s+<', '> <', text)
    text = re.sub(r'><', '> <', text)
    
    return text

# Optimize WORD tags (BOLD, ITALIC, UNDERLINE)
def optimize_word_tags(text):
    """Clean up text by removing unnecessary formatting tags."""

    # Remove WORD tags if they are around punctuation, numbers and single letters (from "<BOLD-> [char]] <-BOLD>" to "")
    text = re.sub(r'<BOLD->\s*([:,.;?!])\s*<-BOLD>', r' \1 ', text)
    text = re.sub(r'<BOLD->\s*([A-Za-z])\s*<-BOLD>', r' \1 ', text)
    text = re.sub(r'<BOLD->\s*(\d+)\s*<-BOLD>', r' \1 ', text)
    
    # Optimize multiple WORD tags
    text = re.sub('<-BOLD>\s*<BOLD->', '', text)
    #text = re.sub('<-ITALIC>\s*<ITALIC->', '', text)
    #text = re.sub('<-UNDERLINE>\s*<UNDERLINE->', '', text)

    # Ensure single space between tags
    text = re.sub(r'>\s+<', '> <', text)
    text = re.sub(r'><', '> <', text)

    # Normalize spaces
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text

# Format spaced headers (from "Q 2 2 0 2 3 E A R N I N G S" to "Q2 2023 EARNINGS")
def format_spaced_headers(text):
    # Improved pattern to identify spaced headers - looks for sequences of 3+ uppercase letters with spaces
    spaced_header_pattern = r'(?<!\S)(?<!<)(?<![\w-])([A-Z](?:\s+[A-Z]){2,})(?!\w)(?![^<]*>)(?!\S)'

    # Function to process each matched header
    def process_header(match):
        spaced_text = match.group(0)

        # First approach: Split by multiple spaces (2 or more)
        if re.search(r'\s{2,}', spaced_text):
            words = re.split(r'\s{2,}', spaced_text)
            condensed_words = [re.sub(r'\s+', '', word) for word in words]
            return ' '.join(condensed_words)
        else:
            # For headers with single spaces between letters but no clear word boundaries
            condensed = re.sub(r'\s+', '', spaced_text)

            # Insert spaces before uppercase letters that follow lowercase or numbers
            spaced = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', condensed)

            # For dates like "2 0 2 3", keep them together
            spaced = re.sub(r'(\d)\s+(\d)', r'\1\2', spaced)           

            return spaced

    # Apply the regex substitution with the processing function
    text = re.sub(spaced_header_pattern, process_header, text)

    return text

# Remove repeating punctuation characters
def remove_repeating_punctuation(text):
    """Remove repeating punctuation characters."""
    # Handle cases where punctuation is separated by spaces
    text = re.sub(r'([.!?](\s+))\1{2,}', '', text)
    
    # Handle continuous repeating punctuation (like "............")
    text = re.sub(r'([.!?])\1{2,}', '', text)
    
    # Handle cases where dots are mixed with spaces in long sequences
    text = re.sub(r'([.]\s*){2,}', '', text)
    
    return text

# Change WORD1 WORD2 into Word1 Word2
def normalize_adjacent_uppercase_words(text):
    """Convert likely names (adjacent uppercase words) to Title Case."""
    # Change "OPERATOR" to "Operator"
    text = re.sub(r'\bOPERATOR\b', 'Operator', text)

    # Pattern for two or more adjacent uppercase words, optionally with a middle initial
    pattern = r'\b([A-Z][A-Z\'\-]+)(\s+[A-Z]\.?\s+)?(\s+[A-Z][A-Z\'\-]+)\b' # original
    #pattern = r'\b([A-Z][A-Z\'\-]+)(?:\s+([A-Z]\.?)?\s+)?([A-Z][A-Z\'\-]+)\b'  # supposed to tackle apostrophes

    def convert_to_title(match):
        first = match.group(1).title()
        middle = match.group(2) if match.group(2) else ''
        last = match.group(3).title()
        return f"{first}{middle}{last}"
    
    return re.sub(pattern, convert_to_title, text)

# Replace special characters, add TEXT tags
def add_text_tags(text):
    """Clean text by handling encoding issues and removing problematic characters."""
    # Handle encoding issues with round-trip conversion
    try:
        # Convert to bytes and back with explicit error handling
        text_bytes = text.encode('utf-8', errors='ignore')
        text = text_bytes.decode('utf-8', errors='ignore')
    except (UnicodeError, AttributeError):
        pass

    # Dictionary of other common substitutions for financial documents
    replacements = {
        '�': '',
        '\ufffd': '',
        '\u2022': '•',  # bullet point
        '\u2018': "'",  # left single quote
        '\u2019': "'",  # right single quote
        '\u201c': '"',  # left double quote
        '\u201d': '"',  # right double quote
        '\u2013': '-',  # en-dash
        '\u2014': '--',  # em-dash
        '\u00a9': '',  # copyright symbol
        '\u00a0': ' <NBSP> ',  # non-breaking space
        '\f': ' <PAGEBREAK> ',  # form feed / page break
        '\n': ' <TAG_2> ',  # line break
        '\t': ' <TAB> ',  # tab
    }

    # Apply all replacements
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)

    # Preserve multiple spaces for regex pattern identification
    text = re.sub(r'[ ]{2,}', ' <TAG_3> ', text)
    
    # Consolidate <TAG_3> <TAG_2> into <TAG_4>
    text = re.sub(r'<TAG_3>\s*<TAG_2>', ' <TAG_4> ', text)
    # text = re.sub(r'<TAG_2> <TAG_3>', '<TAG_4>', text) # optional

    # Ensure single space between tags
    text = re.sub(r'>\s+<', '> <', text)
    text = re.sub(r'><', '> <', text)

    # Normalize spaces
    text = re.sub(r'\s+', ' ', text).strip()

    # Strip leading/trailing whitespace
    # text = text.strip()

    return text

# Fix tag spacing (optional use only if you see issues with tag spacing)
def fix_tag_spacing(text):
    """Fix tag spacing issues that commonly occur in Q/A sections"""
    # Fix all TAG_# variations (TAG_2, TAG_3, TAG_4, etc.)
    text = re.sub(r'<\s*T\s*A\s*G\s*_\s*(\d+)\s*>', r'<TAG_\1>', text)

    # Fix BOLD tags
    text = re.sub(r'<\s*B\s*O\s*L\s*D\s*-\s*>', r'<BOLD->', text)
    text = re.sub(r'<\s*-\s*B\s*O\s*L\s*D\s*>', r'<-BOLD>', text)
    return text

# Check if a text span is a decorative marker (like large Q/A letters)
def is_decorative_marker(span):
    """Check if a text span is a decorative marker (like large Q/A letters)."""
    # Check for characteristics of decorative Q/A markers
    is_large_text = span.get("size", 0) > 18  # Q/A markers are typically very large
    # is_special_font = "Univers-Condensed" in str(span.get("font", ""))
    #is_single_letter = len(span.get("text", "").strip()) == 1
    #is_qa_letter = span.get("text", "").strip().upper() in ["Q", "A"]
    
    return is_large_text #and is_special_font # and is_single_letter and is_qa_letter

# Clean up after tagging
def clean_special_characters(text):
    """Clean text by handling encoding issues and removing problematic characters."""
    # Dictionary common substitutions
    replacements = {
        '�': '',
        '\ufffd': '',
        '\u2022': '•',  # bullet point
        '\u2018': "'",  # left single quote
        '\u2019': "'",  # right single quote
        '\u201c': '"',  # left double quote
        '\u201d': '"',  # right double quote
        '\u2013': '-',  # en-dash
        '\u2014': '--',  # em-dash
        '\u00a9': '',  # copyright symbol

    }

    # Apply all replacements
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)

    return text

# MAIN TEXT PROCESSING PIPELINE
def text_processing_pipeline(pdf_path, config_data, debug_mode=False):
    """Extract text with formatting from PDF, including text from images."""

    doc = pymupdf.open(pdf_path)
    full_text = ""

    for page_num in range(doc.page_count):
        page = doc.load_page(page_num)
        page_text = page.get_text("text")
        page_text = clean_special_characters(page_text)
        lines = page_text.split('\n')
        cleaned_lines = []
        cleaned_lines_with_tags = []
        for line in lines:
            # Remove leading punctuation and spaces using regex
            # ^ matches the beginning of the string
            # [\s\p{P}]+ matches one or more spaces or punctuation characters
            cleaned_line = re.sub(r'^[\s!"#$%&\'*+,-./:;<=>?@[\\\]^_`{|}~]+', '', line)  # ()
            cleaned_line = normalize_adjacent_uppercase_words(cleaned_line)

            # remove lines that contain only one symbol after removing extra spaces
            if len(cleaned_line.strip()) > 1:
                cleaned_lines.append(cleaned_line)
            cleaned_lines_with_tags.append(cleaned_line + "<TAG_2>")
            
        # Cleaning
        # page_text = normalize_adjacent_uppercase_words(page_text)  # bring all names into title case

        # Collate lines and add to full text
        page_text = '\n'.join(cleaned_lines_with_tags)
        full_text += page_text + "\n<PAGE_BREAK>\n"

    
    # Get cleaning parameters
    #cleaning_params = get_cleaning_parameters(config_data)
    #keep_bold_tags = cleaning_params["keep_bold_tags"]
    #keep_italics_tags = cleaning_params["keep_italics_tags"]
    #keep_underline_tags = cleaning_params["keep_underline_tags"]
    
    if debug_mode:
        diagnostics_folder = get_test_mode_info(config_data)["diagnostics_folder"]
        if not os.path.exists(diagnostics_folder):
            os.makedirs(diagnostics_folder)
            print(f"Created directory: {diagnostics_folder}")
        file_path = os.path.join(diagnostics_folder, 'extracted_text.txt')
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(full_text)
        print(f"Formatted text saved to {file_path}")

    doc.close()
    return full_text


### 3. EXTRACTING POTENTIAL SPEAKER ATTRIBUTIONS TO ENHANCE LLM ###

# Helper function to extract context around a pattern
def extract_pattern_context(text, pattern_text, context_chars=10):
    """Extract surrounding context for a pattern in text."""
    pattern_pos = text.find(pattern_text)
    if pattern_pos == -1:
        return "Not found in text"

    context_start = max(0, pattern_pos - context_chars)
    context_end = min(len(text), pattern_pos + len(pattern_text) + context_chars)
    return text[context_start:context_end]

# Helper function to format patterns with context
def format_pattern_with_context(patterns, text):
    """Format a list of patterns with their context into a string."""
    formatted_output = ""
    for i, pattern in enumerate(patterns):
        formatted_output += f"Pattern {i+1}: {pattern}\n"
        context = extract_pattern_context(text, pattern)
        formatted_output += f"Context: \"{context}\"\n\n"
    return formatted_output

# Combined function to extract all potential speaker attributions
def extract_speaker_attributions(text, nlp, config_data=None, debug_mode=False):
    """
    Extract potential speaker attributions using both SpaCy NER and custom matcher.
    Returns both a list of unique attributions and a formatted string with context.
    """
    # Lists to collect all patterns
    all_patterns = []
    ner_patterns = []
    matcher_patterns = []

    # Add custom tags to SpaCy tokenizer
    custom_tags = ["<TAG_2>", "<TAG_3>", "<TAG_4>", "<BOLD->", "<-BOLD>"]
    for tag in custom_tags:
        special_case = [{ORTH: tag}]
        nlp.tokenizer.add_special_case(tag, special_case)
    
    # PART 1: Extract patterns using SpaCy NER
    doc = nlp(text)
    potential_speakers = []

    # Get all PERSON entities
    for ent in doc.ents:
        if ent.label_ == "PERSON":
            start_char = max(0, ent.start_char - 2)
            name = text[start_char:ent.end_char]

            potential_speakers.append({
                "name": name,
                "start": start_char,
                "end": ent.end_char
            })

    # Define tags and punctuation to look for
    formatting_tags = ["<TAG_2>", "<TAG_3>", "<TAG_4>", "<BOLD->"]
    punctuation_marks = [":", "-", ",", ">"]

    # Process each potential speaker
    for speaker in potential_speakers:
        pre_context = text[max(0, speaker["start"] - 10):speaker["start"]]
        post_context = text[speaker["end"]:min(len(text), speaker["end"] + 15)]

        if any(tag in pre_context for tag in formatting_tags):
            # Find start position (first tag)
            start_idx = speaker["start"]
            for tag in formatting_tags:
                tag_pos = pre_context.rfind(tag)
                if tag_pos != -1:
                    start_idx = speaker["start"] - (len(pre_context) - tag_pos)
                    break

            # Find end position (first punctuation)
            end_idx = speaker["end"]
            for mark in punctuation_marks:
                mark_pos = post_context.find(mark)
                if mark_pos != -1:
                    potential_end = speaker["end"] + mark_pos + 1
                    if end_idx == speaker["end"] or potential_end < end_idx:
                        end_idx = potential_end
                        break

            attribution = text[start_idx:end_idx]
            if attribution and attribution not in ner_patterns:
                ner_patterns.append(attribution)
                all_patterns.append(attribution)

    # PART 2: Extract patterns using SpaCy matcher
    matcher = Matcher(nlp.vocab)

    # Pattern 1: Name and punctuation:
    matcher.add("NAME_PUNCTUATION", [
        [
            {"POS": "PROPN"},
            {"POS": "PROPN", "OP": "+"},
            {"IS_SPACE": True, "OP": "*"},
            {"IS_PUNCT": True}
        ]
    ])

    # Pattern 2: Name and tag:
    matcher.add("NAME_TAG", [
        [
            {"IS_SENT_START": True},
            {"POS": "PROPN"},
            {"POS": "PROPN", "OP": "+"},
            {"IS_SPACE": True, "OP": "*"},
            {"TEXT": {"REGEX": "<[^>]+>"}}
        ]
    ])

    # Pattern 3: Name with non-name text:
    matcher.add("NAME_von_NAME", [
        [
            {"IS_SENT_START": True},
            {"POS": "PROPN", "OP": "+"},
            {"POS": {"NOT_IN": ["PROPN"]}, "OP": "*"},
            {"POS": "PROPN", "OP": "+"},
            {"IS_SPACE": True, "OP": "*"},
            {"TEXT": {"REGEX": "<[^>]+>"}}
        ]
    ])

    """
    # Pattern 1: <TAG> Name Surname <TAG>
    matcher.add("TAG_NAME_SURNAME_TAG", [
        [
            {"TEXT": {"REGEX": "<[^>]+>"}},     # Opening tag
            {"IS_TITLE": True},                  # First name
            {"IS_TITLE": True, "OP": "?"},       # Optional middle initial
            {"TEXT": ".", "OP": "?"},            # Optional period
            {"IS_TITLE": False, "OP": "?"},      # Optional lowercase part
            {"IS_TITLE": True},                  # Surname
            {"TEXT": "-", "OP": "?"},            # Optional hyphen
            {"IS_TITLE": True, "OP": "?"},       # Optional second surname
            {"TEXT": {"REGEX": "<[^>]+>"}}       # Closing tag
        ]
    ])

    # Pattern 2: <TAG> <TAG> Name Surname
    matcher.add("TAG_TAG_NAME_SURNAME", [
        [
            {"TEXT": {"REGEX": "<[^>]+>"}},      # First tag
            {"TEXT": {"REGEX": "<[^>]+>"}},      # Second tag
            {"IS_TITLE": True},                  # First name
            {"IS_TITLE": True, "OP": "?"},       # Optional middle initial
            {"TEXT": ".", "OP": "?"},            # Optional period
            {"IS_TITLE": False, "OP": "?"},      # Optional lowercase part
            {"TEXT": {"REGEX": "[A-Z][a-z]*'?[A-Z]?[a-z]+"}},  # Surname with possible apostrophe
            {"TEXT": "-", "OP": "?"},            # Optional hyphen
            {"IS_TITLE": True, "OP": "?"}        # Optional second surname
        ]
    ])

    # Pattern 3: Name Letter(s) APOSTROPHE Surname SEPARATOR
    matcher.add("NAME_LETTER_APOSTROPHE_SURNAME_TAG_SEPARATOR", [
        [
            {"IS_TITLE": True},                  # First name
            {"IS_TITLE": True, "OP": "?"},       # Optional middle initial
            {"TEXT": ".", "OP": "?"},            # Optional period
            {"IS_TITLE": False, "OP": "?"},      # Optional lowercase part
            {"SHAPE": {"REGEX": "Xx+(?:'Xx+)?"}}, # Surname with possible apostrophe
            {"TEXT": "-", "OP": "?"},            # Optional hyphen
            {"IS_TITLE": True, "OP": "?"},       # Optional second surname
            {"TEXT": {"REGEX": ":|\\s-"}}        # Separator
        ]
    ])

    # {"TEXT": {"REGEX": "[A-Za-z][a-z]*'[A-Za-z]+"}}, # Surname with possible apostrophe
    
    # Pattern 4: Name Surname - Company - Job Title <TAG>
    matcher.add("NAME_SURNAME_COMPANY_JOB_TITLE_TAG", [
        [
            {"IS_TITLE": True},                  # First name
            {"IS_TITLE": True, "OP": "?"},       # Optional middle initial
            {"TEXT": ".", "OP": "?"},            # Optional period
            {"IS_TITLE": False, "OP": "?"},      # Optional lowercase part
            {"IS_TITLE": True},                  # Surname
            {"TEXT": {"REGEX": "–|-|,"}},        # Separator
            {"IS_ALPHA": True},                  # Company name (first word)
            {"IS_ALPHA": True, "OP": "*"},       # Additional company words
            {"TEXT": {"REGEX": "–|-|,"}},        # Another dash/separator
            {"IS_ALPHA": True},                  # Position title (first word)
            {"IS_ALPHA": True, "OP": "*"},       # Additional position word
            {"TEXT": {"REGEX": "<[^>]+>"}}       # Closing tag
        ]
    ])
    """
    # Apply matcher
    matches = matcher(doc)
    for match_id, start, end in matches:
        span = doc[start:end]
        matched_text = span.text
        clean_span = re.sub(r'<[^>]+>', '', matched_text).strip()

        if span and matched_text not in matcher_patterns:
            matcher_patterns.append(matched_text)
            if matched_text not in all_patterns:
                all_patterns.append(matched_text)

    # Format results
    ner_formatted = format_pattern_with_context(ner_patterns, text)
    matcher_formatted = format_pattern_with_context(matcher_patterns, text)
    combined_formatted = ner_formatted + matcher_formatted

    # Debug output if requested
    if debug_mode and config_data:
        print(f"Extracted {len(ner_patterns)} potential attributions from NER")
        print(f"Extracted {len(matcher_patterns)} potential attributions from matcher")
        print(f"Combined into {len(all_patterns)} unique attributions")

        diagnostics_folder = get_test_mode_info(config_data)["diagnostics_folder"]
        if not os.path.exists(diagnostics_folder):
            os.makedirs(diagnostics_folder)
            print(f"Created directory: {diagnostics_folder}")

        file_path = os.path.join(diagnostics_folder, 'spacy_patterns.txt')
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(combined_formatted)

    return all_patterns, combined_formatted

# Extract potential attributions for "Operator" with preceeding formatting tags
def get_operator_attributions(text):
    operator_pattern = r'(<[^>]+>)+\s*Operator'
    operator_attributions = ""

    for match in re.finditer(operator_pattern, text):
        operator_with_tags = match.group(0)
        operator_attributions += f"Operator with tags: {operator_with_tags}\n"

    # remove duplicates
    operator_attributions = list(set(operator_attributions))

    return operator_attributions


### 4. SPEAKER ATTRIBUTION EXTRACTION USING LLM ###

# API call to extract speaker attributions
def API_call(text, spacy_patterns, operator_attributions, config_data, debug_mode=False):
    # Construct the prompt according to the specified format
    prompt = f"""User: You are an AI assistant specialized in extracting speaker attributions from earning call transcripts.

    <goal>
    Extract all speaker attributions and call details from the transcript.    
    </goal>

    Here is the transcript to analyze:
    <transcript>
    {text}
    </transcript>

    Here are the potential speaker attributions extracted by SpaCy:
    <spacy_suggestions>
    {spacy_patterns}
    </spacy_suggestions>

    Here are the potential operator attributions:
    <operator_suggestions>
    {operator_attributions}
    </operator_suggestions>

    <instructions>
    Follow these step by step instructions:
    Step 1: Find the names of all call participants, including variations and misspellings. Use the spacy suggestions to help you.
    Step 2: For each participant, find their job title and companies, including variations.
    Step 3: Go through the whole text in small overlapping chunks to idenitify all variants of speaker attributions including leading and tailing tags, names, titles and companies (if available), punctuation marks.
    Step 4. For each speaker with a single attribution check again for other attributions with different formatting.
    Step 5. Identify call details like bank name, call date and reporting period.
    Step 6. Identify the last 10 tokens of the last utterance in the transcript.
    Step 7. Identify header and footer that repeat throughout the transcript, if any.
    Step 8. Return results as a json object.
    </instructions>

    <formatting_tags>
    Formatting tags should be treated as text and should be added to the attribution.
    - Bold text is surrounded by <BOLD-> [speaker attribution or text or punctuation] <-BOLD>
    - Line breaks are marked as <TAG_2>
    - Paragraph breaks are marked as <TAG_4>
    - Multispaces are marked as <TAG_3>
    - Page breaks are marked as <PAGE_BREAK>
    </formatting_tags>

    <attribution_description>
    The speaker attribution :
    1. There are usually two or more variants of the attribution formats for the same speaker. Always include all variants in the output.
    2. Attribution must start on a new line.
    3. Attribution may be on the same line as the speaker's speech or on the next line.
    4. Attribution includes one of the following:
        a) [Speaker Name and Surname] or [Name, Middle Name Initial and Surname]
        b) [Speaker Name and Surname] or [Name, Middle Name Initial and Surname], followed by a [job title], [company], or [job title and company]
    5. The job title and company, if present, can be separated from the speaker name and from each other by a punctuation mark like a colon or a dash, a formatting tag like <BOLD-> or <TAG_2> or <TAG_2>, or a combination.
    6. Attribution must end with a punctuation mark like a colon or a dash, a formatting tag like <-BOLD> or <TAG_2>, or both.
    6. Attribution never includes the text of the speech of the speaker.
    </attribution_description>
    
    <attribution_search_guidelines>
    Guidelines for searching for speaker attribution:
    - Always check the whole transcript from the beginning to the end.
    - Always look for attributions everywhere in paragraphs, both at the beginning, middle and at the end of paragraphs, particularly after sentence endings.
    - Always include all variations of attributions for each speaker even if the difference is in one character.   
    - Pay attention to the job title and company name variations.
    - Speaker attributions always alternate with the speaker's speech.
    - Speaker attributions cannot appear next to each other. If they do, they are not speaker attributions. There should always be a speech between attributions.
    - If two adjacent speeches are from the same speaker, then there must be an attribution for another speaker between them. Find it.
    - All variants of operator attibutions should always contain the word "Operator" and variations of leading and trailing formatting tags.
    - Always include ALL attributions variants even if minor.
    
    Additionally:
    - If speaker's name is separated from the job title or company by a text segment that is not a formatting tag, then only speaker name should be a part of attribution.
    - If speaker's name is followed by a text segment that is not a formatting tag, then only speaker name should be a part of attribution.
    </attribution_search_guidelines>
    
    <examples>    
    Examples of attributions that contain speaker name only:
    * "(attribution starts) Full name <TAG_2> (attribution ends) (new line) Thank you. I'd like to present our quarterly results."
    * "(attribution starts) Full name - (attribution ends) (same line) Thank you. I'd like to present our quarterly results."
    * "(attribution starts) Full name: <TAG_2>(attribution ends) (new line) Thank you. I'd like to present our quarterly results."
    * "(attribution starts) Full name: (attribution ends) (same line) Thank you. I'd like to present our quarterly results."
    If the same speaker has various attributions, then all attributions should be included into the output.

    Examples of attributions that contain speaker name with job title, company, or job title and company:
    * "(attribution starts) Full name - Company - Job Title (attribution ends) (new line) Thank you. I'd like to present our quarterly results."
    * "(attribution starts) Full name - Company - Job Title (attribution ends) (same line) Thank you. I'd like to present our quarterly results."
    * "(attribution starts) Full name - CEO, TechCorp: (attribution ends) (new line) Thank you. I'd like to present our quarterly results."
    * "(attribution starts) Full name, CFO: (attribution ends) (same line) Thank you. I'd like to present our quarterly results."
    * "(attribution starts) Full name • Senior VP: (attribution ends) (new line) Thank you. I'd like to present our quarterly results."
    * "(attribution starts) Full name • Senior VP: (attribution ends) (same line) Thank you. I'd like to present our quarterly results."

    Example of complex attributions with multiple tags and punctuation:
    * "(attribution starts) Jamie Dimon <TAG_3> <TAG_2> Chairman & Chief Executive Officer, JPMorgan Chase & Co. <TAG_3><TAG_2> (attribution ends)"
    
    There are often multiple attribution formatting variations of the same speaker, for example:
    * "(attribution starts) Full name: (attribution ends) Thank you. I'd like to present our quarterly results."
    * "(attribution starts) Full name <TAG_2> (attribution ends) Thank you. I'd like to present our quarterly results."
    In such cases all variants should be included in the output.

    There are often multiple variations of the Operator attributions, for example:
    * "OPERATOR:"
    * "OPERATOR <TAG_2>"
    In such cases all variants should be included in the output.
    
    Spelling and Name Variations:
    * "(attribution begins here) Michael J. Thompson: (attribution ends here)"
    * "(attribution begins here)  Mike Thompson: (attribution ends here)"
    In such cases all variants should be included in the output.
    
    Job Title and Company Separation:
    * "(attribution begins here) Full name: <TAG_2> (attribution ends here) Thank you. I am (Job Title) and I'd like present our (Company Name) quarterly results."
    In such cases only "<TAG_4> Full name: <TAG_2>" should be considered attribution because there is a text between the name and job title.
    
    Company Name Variations:
    * "(attribution begins here) Full name <TAG_2> (Full company name) <TAG_2> (attribution ends here) Thank you. I'd like to present our quarterly results."
    * "(attribution begins here) Full name <TAG_2> (Company name abbreviation) <TAG_2> (attribution ends here) Thank you. I'd like to present our quarterly results."
    In such cases all variants should be included in the output.
    </examples>

    REMEMBER: The output should include ALL variants of the attributions for every speaker.
    
    Here's an example of the expected JSON structure (with generic placeholders):
    <jsonexample>
    {{
    "bank_name": "Example Bank",
    "call_date": "YYYY-MM-DD",
    "reporting_period": "Q-YYYY",
    "header_pattern": "HEADER_PATTERN",
    "footer_pattern": "FOOTER_PATTERN",
    "last_utterance_tokens": "LAST_10_TOKENS",
    "participants": [
        {{
        "speaker_name_variants": ["John Doe", "Jon Doe", "John Do"],
        "speaker_title_variants": ["Chief Executive Officer", "CEO"],
        "speaker_company_variants": ["Example Bank", "Example Bank Inc.", "EB"],
        "speaker_attributions": ["<TAG_2> JOHN DOE:", "<BOLD-> John Doe - CEO - Example Bank <-BOLD>", "John Doe - EB - CEO <TAG_3>"]
        }}
    ]
    }}
    </jsonexample>

    <json_validation>
    Your response must be valid, parseable JSON. Ensure:
    - Use single curly braces for objects, not double
    - All strings are properly quoted
    - No trailing commas in arrays or objects
    - All keys and values follow proper JSON syntax
    - Test your JSON structure mentally before providing it as output
    </json_validation>

    It should be possible to parse the JSON object from the response.
    Provide only the JSON object as your final response, with no additional text or explanations.
    """
    api_key_name, model, input_cost_per_million, output_cost_per_million = get_api_setup(config_data)
    api_key = os.getenv(api_key_name)
    client = anthropic.Anthropic(api_key=api_key)

    if debug_mode:
        print(f"Running API call using {model}...")

    try:
        message = client.messages.create(
            model="claude-3-7-sonnet-20250219",  # claude-3-opus-20240229 claude-3-7-sonnet-20250219
            max_tokens=4096,
            system="You are an expert in finding speaker attributions in the earnings call transcripts.",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            top_p=0.7,
            # top_k=20
        )

        # Get token counts from the API response and calculate cost
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        total_tokens = input_tokens + output_tokens

        input_cost = (input_tokens / 1_000_000) * float(input_cost_per_million)
        output_cost = (output_tokens / 1_000_000) * float(output_cost_per_million)
        total_cost = input_cost + output_cost

        # Get the response text
        if not message.content or len(message.content) == 0:
            return {
                "error": "Empty response from API"
            }

        response_text = message.content[0].text

        diagnostics_folder = get_test_mode_info(config_data)["diagnostics_folder"]  # always use this folder for diagnostics
        
        # Always save the raw response for debugging
        if debug_mode:
            raw_response_path = os.path.join(diagnostics_folder, 'api_response_raw.txt')
            with open(raw_response_path, 'w', encoding='utf-8') as f:
                f.write(response_text)
            print(f"Raw API response saved to {raw_response_path}")

        # Try to parse the response as JSON
        try:
            # Check if the response is wrapped in markdown code blocks
            clean_response = response_text
            if response_text.startswith("```json"):
                end_marker = "```"
                end_pos = response_text.rfind(end_marker)
                if end_pos > 0:
                    clean_response = response_text[7:end_pos].strip()
                    
                    if debug_mode:
                        cleaned_path = os.path.join(diagnostics_folder, 'api_response_cleaned.txt')
                        with open(cleaned_path, 'w', encoding='utf-8') as f:
                            f.write(clean_response)
                        print(f"Cleaned API response saved to {cleaned_path}")
            
            json_response = json.loads(clean_response)
            parsed_successfully = True
            
            if debug_mode:
                parsed_path = os.path.join(diagnostics_folder, 'api_response_parsed.json')
                with open(parsed_path, 'w', encoding='utf-8') as f:
                    json.dump(json_response, f, indent=2)
                print(f"Parsed JSON saved to {parsed_path}")
                
        except json.JSONDecodeError as e:
            parsed_successfully = False
            json_response = None
            
            if debug_mode:
                print(f"JSON parsing error: {str(e)}")
                error_path = os.path.join(diagnostics_folder, 'api_response_error.txt')
                with open(error_path, 'w', encoding='utf-8') as f:
                    f.write(response_text)
                    f.write("\n\n--- JSON PARSE ERROR ---\n")
                    f.write(str(e))
                print(f"Error details saved to {error_path}")

        return {
            "response": response_text,
            "parsed_json": json_response if parsed_successfully else None,
            "json_parsed_successfully": parsed_successfully,
            "token_counts": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens
            },
            "cost_estimate": {
                "total_cost_usd": total_cost
            }
        }

    except Exception as e:
        if debug_mode:
            print(f"API call exception: {str(e)}")
        return {
            "error": str(e)
        }


### 5. TEXT PARSING AND CLEANING ###

"""
def remove_unused_sections(metadata_file: str, debug_mode: bool) -> str:
    
    Remove sections before the presentation start page.
    
    Args:
        transcript_text (str): Full transcript text
        metadata_file (str): Path to the metadata file
        
    Returns:
        str: Transcript text starting from the presentation start page
        
    Raises:
        KeyError: If required metadata fields are missing
        ValueError: If page numbers are invalid
    
    if debug_mode:
        print(f"Removing unused sections...")

    metadata = load_metadata(metadata_file, debug_mode)
    text = load_transcript(metadata['path_to_transcript_txt'], debug_mode)

    try:
        presentation_start_page = int(metadata['presentation_section_details'][0]['presentation_section_start_page_number'])
    except (KeyError, IndexError) as e:
        raise KeyError(f"Required metadata fields are missing: {str(e)}")

    # print(presentation_start_page)

    pages = text.split('<PAGE_BREAK>')
    if presentation_start_page < 1 or presentation_start_page > len(pages):
        raise ValueError(f"Invalid start page number: {presentation_start_page}, total pages={len(pages)}")

    # Select pages starting from the presentation start page
    selected_text = "<PAGE_BREAK>".join(pages[presentation_start_page - 1:])

    return selected_text
"""
# De-duplicate leading tags in attributions (from <TAG_1> <TAG_1> to <TAG_1>)
def remove_leading_duplicate_tags(attribution):
    # Pattern to match any opening tag
    tag_pattern = r'<[A-Z_0-9]+>'

    # Find all tags at the beginning of the string
    match = re.match(r'^(\s*(' + tag_pattern + r'\s*)+)', attribution)

    if match:
        # Get the entire matched section (all leading tags)
        leading_tags_section = match.group(1)

        # Find all individual tags in this section
        tags = re.findall(tag_pattern, leading_tags_section)

        # Remove duplicates while preserving order
        unique_tags = []
        for tag in tags:
            if not unique_tags or tag != unique_tags[-1]:
                unique_tags.append(tag)

        # Create the new leading section with single space between tags
        new_leading_section = ' '.join(unique_tags) + ' '

        # Replace the original leading section with the deduplicated one
        result = attribution.replace(leading_tags_section, new_leading_section, 1)
        return result

    return attribution

def get_utterances(text, api_response, config_data, debug_mode=False):
    """
    Extract utterances from transcript text using speaker attributions from API response.
    
    Args:
        text (str): The transcript text
        api_response (dict): The response from the API call containing speaker attributions
        debug_mode (bool): Whether to print debug information
        
    Returns:
        list: List of dictionaries containing speaker and their utterance
    """
    
    # Check if api_response is None or doesn't have parsed_json
    if api_response is None:
        if debug_mode:
            print("API response is None")
        return []
    
    # Get parsed JSON from API response
    parsed_json = api_response.get("parsed_json")
    
    # Check if parsed_json is None
    if parsed_json is None:
        if debug_mode:
            print("Parsed JSON is None - JSON parsing likely failed")
            if "response" in api_response:
                print("Raw API response:", api_response["response"][:200] + "...")  # Print first 200 chars
        return []
    
    # Check if participants key exists
    if "participants" not in parsed_json:
        if debug_mode:
            print("No 'participants' key in parsed JSON")
            print("Available keys:", list(parsed_json.keys()))
        return []
    
    # Collect all speaker attributions into a list
    all_attributions = []
    for participant in parsed_json.get("participants", []):
        speaker_name = participant.get("speaker_name_variants", ["Unknown"])[0]
        for attribution in participant.get("speaker_attributions", []):
            all_attributions.append({
                "speaker_name": speaker_name,
                "attribution": attribution
            })

    if debug_mode:
        print(f"Found {len(all_attributions)} speaker attributions")
        print("Writing all cleaned attributions to all_attributions.txt...")
        
        # Create a file to save all attributions for debugging
        diagnostics_folder = get_test_mode_info(config_data)["diagnostics_folder"]
        file_path = os.path.join(diagnostics_folder, 'all_attributions.txt')
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(f"Total attributions found: {len(all_attributions)}\n\n")
            for i, attr in enumerate(all_attributions):
                f.write(f"Attribution {i+1}:\n")
                f.write(f"  Speaker: {attr['speaker_name']}\n")
                f.write(f"  Text: {attr['attribution']}\n\n")
        
    if not all_attributions:
        return []

    # De-duplicate leading tags in attributions
    all_attributions = [{
        "speaker_name": attr["speaker_name"],
        "attribution": remove_leading_duplicate_tags(attr["attribution"])
    } for attr in all_attributions]
    
    # Find all matches for all attributions directly in the text
    all_matches = []
    for attr_info in all_attributions:
        attribution = attr_info["attribution"]
        # Find all occurrences of this attribution in the text
        start_pos = 0
        while True:
            pos = text.find(attribution, start_pos)
            if pos == -1:
                break
            all_matches.append({
                "speaker_name": attr_info["speaker_name"],
                "start": pos,
                "end": pos + len(attribution)
            })
            start_pos = pos + 1  # Move past the current match to find the next one
    
    # Sort matches by their position in the text
    all_matches.sort(key=lambda x: x["start"])
    
    if not all_matches:
        return []
    
    # Extract utterances
    utterances = []
    for i in range(len(all_matches) - 1):
        current_match = all_matches[i]
        next_match = all_matches[i + 1]
        
        # Get the text between the end of the current attribution and the start of the next
        utterance_text = text[current_match["end"]:next_match["start"]]
        
        utterances.append({
            "speaker": current_match["speaker_name"],
            "utterance": utterance_text.strip()
        })
    
    # Handle the last speaker's utterance (from last attribution to end of text)
    last_match = all_matches[-1]
    last_utterance = text[last_match["end"]:]
    utterances.append({
        "speaker": last_match["speaker_name"],
        "utterance": last_utterance.strip()
    })
    
    if debug_mode:
        print(f"Extracted {len(utterances)} utterances")
    
    return utterances

def clean_utterances(utterances: list, api_response: dict) -> list:
    cleaned_utterances = []

    # Remove empty utterances
    utterances = [utterance for utterance in utterances if utterance['utterance'].strip()]

    # Count for each cleaning type specificed below
    debug_counts = {
        'angle_brackets': 0,
        'parentheses': 0,
        # 'double_quotes': 0,
        # 'single_quotes': 0,
        'double_slashes': 0,
        'backslashes': 0
    }

    # This should clear the utterance from all tags
    for utterance in utterances:
        text = utterance['utterance']

        # Remove text within <>
        angle_brackets_count = len(re.findall(r'<[^>]*>', text))
        text = re.sub(r'<[^>]*>', '', text)
        debug_counts['angle_brackets'] += angle_brackets_count

        # Remove text within ()
        parentheses_count = len(re.findall(r'\([^)]*\)', text))
        text = re.sub(r'\([^)]*\)', '', text)
        debug_counts['parentheses'] += parentheses_count

        # Remove text within //
        double_slashes_count = len(re.findall(r'//[^/]*//', text))
        text = re.sub(r'//[^/]*//', '', text)
        debug_counts['double_slashes'] += double_slashes_count

        # Remove text within \\
        backslashes_count = len(re.findall(r'\\[^\\]*\\', text))
        text = re.sub(r'\\[^\\]*\\', '', text)
        debug_counts['backslashes'] += backslashes_count

        # Remove extra white spaces
        text = ' '.join(text.split())

        # Create a new utterance with a UUID if it doesn't have one
        cleaned_utterance = {
            'speaker': utterance['speaker'],
            'utterance': text
        }
        
        # Add UUID if it exists in the original utterance, otherwise generate a new one
        if 'uuid' in utterance:
            cleaned_utterance['uuid'] = utterance['uuid']
        else:
            cleaned_utterance['uuid'] = str(uuid.uuid4())
            
        cleaned_utterances.append(cleaned_utterance)

    # Remove header pattern from each utterance's text (not removing the whole utterance)
    header_pattern = api_response.get("header_pattern", None) if isinstance(api_response, dict) else None
    if header_pattern:  # Only process if header_pattern exists
        cleaned_header_pattern = re.sub(r'<(?:TAG_2|TAG_3|TAG_4|BOLD-|-BOLD)>', '', header_pattern)
        for utterance in utterances:
            # Replace the matching pattern with an empty string, keeping the rest of the text
            cleaned_text = re.sub(cleaned_header_pattern, '', utterance['utterance'], flags=re.IGNORECASE)
            # Create a new utterance with the cleaned text
            cleaned_utterance = utterance.copy()  # Copy to preserve other fields
            cleaned_utterance['utterance'] = cleaned_text
            cleaned_utterances.append(cleaned_utterance)

    # Remove footer from all utterances
    footer_pattern = api_response.get("footer_pattern", None) if isinstance(api_response, dict) else None
    if footer_pattern:  # Only process if footer_pattern exists
        cleaned_footer_pattern = re.sub(r'<(?:TAG_2|TAG_3|TAG_4|BOLD-|-BOLD)>', '', footer_pattern)
        for utterance in utterances:
            # Replace the matching pattern with an empty string, keeping the rest of the text
            cleaned_text = re.sub(cleaned_footer_pattern, '', utterance['utterance'], flags=re.IGNORECASE)
            # Create a new utterance with the cleaned text
            cleaned_utterance = utterance.copy()  # Copy to preserve other fields
            cleaned_utterance['utterance'] = cleaned_text
            cleaned_utterances.append(cleaned_utterance)


    return cleaned_utterances

def create_and_save_final_json(api_response, cleaned_utterances, output_path, debug_mode=False):
    """
    Combine the API response and cleaned utterances into a final JSON structure.
    
    Args:
        api_response (dict): The response from the API call containing speaker attributions and metadata
        cleaned_utterances (list): List of cleaned utterances with speaker and text
        debug_mode (bool): Whether to print debug information
        
    Returns:
        dict: Combined JSON with metadata from API and cleaned utterances
    """
    if debug_mode:
        print("Creating final JSON from API response and cleaned utterances...")
    
    # Start with the API response as the base, ensuring we have a dictionary
    parsed_json = api_response.get("parsed_json")
    if parsed_json is None:
        if debug_mode:
            print("Warning: parsed_json is None, creating empty dictionary")
        final_json = {}
    else:
        final_json = copy.deepcopy(parsed_json)
    
    # Add the utterances to the final JSON
    final_json["utterances"] = cleaned_utterances
    
    if debug_mode:
        print(f"Final JSON created with {len(cleaned_utterances)} utterances")
    
    # Save the final JSON to a file
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(final_json, f, indent=2)
    print(f"Final JSON saved to {output_path}")
    
    return final_json


### 6. MAIN FUNCTION ###

def main():
    # Main function to process a PDF transcript and get AI analysis.
    
    parser = argparse.ArgumentParser(description='Extract and analyze earnings call transcript using Claude API.')
    parser.add_argument('pdf_path', nargs='?', help='Path to the PDF transcript (optional, will use test file if not provided)')
    parser.add_argument('--output', '-o', help='Output JSON file path (optional)')
    
    args = parser.parse_args()

    # Load config
    config_data = load_config()

    # Extract test mode and debug mode information
    test_mode_info = get_test_mode_info(config_data)
    test_mode_enabled = test_mode_info['enabled']
    debug_mode = test_mode_info['debug_mode']

    # Extract folder paths
    folder_paths = get_folder_paths(config_data)
    transcripts_pdf_folder = folder_paths['transcripts_pdf_folder']
    final_json_folder = folder_paths['final_json_folder']

    # Determine the file to process
    if args.pdf_path:
        file_path = args.pdf_path
        file_name = file_path.split('/')[-1]
    elif test_mode_enabled:
        print("No PDF path provided, using test file from config.")
        file_name = test_mode_info['file_name']
        file_path = f"{transcripts_pdf_folder}/{file_name}"
    else:
        print("Error: No PDF path provided and test mode is not enabled.")
        return

    # Extract text from PDF
    print(f"Extracting text from {file_path}...")
    full_text = text_processing_pipeline(file_path, config_data, debug_mode)
    

    # Extract potential speaker attributions
    print("Extracting speaker attributions using SpaCy...")
    # nlp = spacy.load("en_core_web_trf")  # optional: transformer model for better accuracy
    nlp = spacy.load("en_core_web_sm")

    potential_attributions, formatted_patterns = extract_speaker_attributions(full_text, nlp, config_data, debug_mode)
    spacy_patterns = (potential_attributions, formatted_patterns)
    operator_attributions = get_operator_attributions(full_text)

    # Make API call
    print("Sending text to API for analysis...")
    api_response = API_call(full_text, spacy_patterns, operator_attributions, config_data, debug_mode)
    
    # Check for errors in the API call result
    if "error" in api_response:
        print(f"Error: {api_response['error']}")
        return
    
    # Get utterances
    utterances = get_utterances(full_text, api_response, config_data, debug_mode)
    
    # Clean utterances
    cleaned_utterances = clean_utterances(utterances, api_response)
    
    # Determine output path
    if args.output:
        output_path = args.output
    else:
        output_path = f"{final_json_folder}/{file_name.replace('.pdf', '_final.json')}"
    
    # Create and save final JSON
    create_and_save_final_json(api_response, cleaned_utterances, output_path, debug_mode)

    # Check if token_counts is available
    if "token_counts" in api_response:
        print(f"Input tokens: {api_response['token_counts']['input_tokens']}")
        print(f"Output tokens: {api_response['token_counts']['output_tokens']}")
        print(f"Total tokens: {api_response['token_counts']['total_tokens']}")
        print(f"Estimated cost: ${api_response['cost_estimate']['total_cost_usd']:.2f}")
    else:
        print("Token counts not available in the API response.")


if __name__ == "__main__":
    main()