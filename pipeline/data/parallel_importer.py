#!/usr/bin/env python3
"""
Downloads a parallel dataset and runs augmentation if needed

Example:
    python pipeline/data/parallel_importer.py \
        --dataset=sacrebleu_aug-mix_wmt19 \
        --output_prefix=$(pwd)/test_data/augtest \
        --src=ru \
        --trg=en
"""

import argparse
import os
import random
import re
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, List

from opustrainer.modifiers.noise import NoiseModifier
from opustrainer.modifiers.placeholders import PlaceholderTagModifier
from opustrainer.modifiers.punctuation import RemoveEndPunctuationModifier
from opustrainer.modifiers.surface import TitleCaseModifier, UpperCaseModifier
from opustrainer.modifiers.typos import TypoModifier
from opustrainer.types import Modifier

from pipeline.common.downloads import compress_file, decompress_file
from pipeline.common.logging import get_logger
from pipeline.data.cjk import handle_chinese_parallel, ChineseType
from pipeline.data.lang_script import get_script_info, is_script_phonemic
from pipeline.data.parallel_downloaders import download, Downloader

random.seed(1111)

logger = get_logger(__file__)


class CompositeModifier:
    """
    Composite modifier runs several modifiers one after another
    """

    def __init__(self, modifiers: List[Modifier]):
        self._modifiers = modifiers

    def __call__(self, batch: List[str]) -> Iterable[str]:
        for mod in self._modifiers:
            batch = list(mod(batch))

        return batch


MIX_PROB = 0.05  # 5% will be augmented in the mix
PROB_1 = 1.0  # 100% chance
PROB_0 = 0.0  # 0% chance
# use lower probabilities than 1 to add inline noise into the mix
# probability 1 adds way too much noise to a corpus
NOISE_PROB = 0.05
NOISE_MIX_PROB = 0.01


def get_typos_probs() -> Dict[str, float]:
    # select 4 random types of typos
    typos = set(random.sample(list(TypoModifier.modifiers.keys()), k=4))
    # set probability 1 for selected typos and 0 for the rest
    probs = {typo: PROB_1 if typo in typos else PROB_0 for typo in TypoModifier.modifiers.keys()}
    return probs


# See documentation for the modifiers in https://github.com/mozilla/translations/blob/main/docs/training/opus-trainer.md#supported-modifiers
modifier_map = {
    "aug-typos": lambda: TypoModifier(PROB_1, **get_typos_probs()),
    "aug-title": lambda: TitleCaseModifier(PROB_1),
    "aug-upper": lambda: UpperCaseModifier(PROB_1),
    "aug-punct": lambda: RemoveEndPunctuationModifier(PROB_1),
    "aug-noise": lambda: NoiseModifier(PROB_1),
    "aug-inline-noise": lambda: PlaceholderTagModifier(NOISE_PROB, augment=1),
    # Built dynamically by build_aug_mix
    "aug-mix": None,
}


def add_alignments(corpus: List[str]) -> List[str]:
    from simalign import SentenceAligner  # type: ignore

    logger.info("Adding the alignments")

    # We use unsupervised aligner here because statistical tools like fast_align require a large corpus to train on
    # This is slow without a GPU and is meant to operate only on small evaluation datasets

    # Use BERT with subwords and itermax as it has a higher recall and matches more words than other methods
    # See more details in the paper: https://arxiv.org/pdf/2004.08728.pdf
    # and in the source code: https://github.com/cisnlp/simalign/blob/master/simalign/simalign.py
    # This will download a 700Mb BERT model from Hugging Face and cache it
    aligner = SentenceAligner(model="bert", token_type="bpe", matching_methods="i")

    alignments = []
    for line in corpus:
        src_sent, trg_sent = line.split("\t")
        sent_aln = aligner.get_word_aligns(src_sent, trg_sent)["itermax"]
        aln_str = " ".join(f"{src_pos}-{trg_pos}" for src_pos, trg_pos in sent_aln)
        alignments.append(aln_str)

    corpus_tsv = [f"{sents}\t{aln}" for sents, aln in zip(corpus, alignments)]
    return corpus_tsv


def build_aug_mix(src: str, trg: str) -> Callable[[], CompositeModifier]:
    """
    Different types of scripts can use different types of mixed augmentations.
    Determine which ones apply here, and create the appropriate mix.
    """
    src_script = get_script_info(src)
    trg_script = get_script_info(trg)

    assert src_script, "The script info must exist for the src language."
    assert trg_script, "The script info must exist for the trg language."

    logger.info("src_script " + repr(src_script))
    logger.info("trg_script " + repr(trg_script))

    modifiers: list[Modifier] = [RemoveEndPunctuationModifier(MIX_PROB)]

    # Bicameral scripts can have their casing augmented.
    if src_script["bicameral"]:
        modifiers.append(TitleCaseModifier(MIX_PROB))
        modifiers.append(UpperCaseModifier(MIX_PROB))

    # Phonemic languages can be misspelled.
    if is_script_phonemic(src_script["type"]):
        modifiers.append(TypoModifier(MIX_PROB, **get_typos_probs()))

    # Noise must be near the end so we don't augment added noise.
    modifiers.append(NoiseModifier(MIX_PROB))

    # The Tag modifier can remove the alignments, so ensure that it is last.
    modifiers.append(PlaceholderTagModifier(NOISE_MIX_PROB, augment=1))

    return lambda: CompositeModifier(modifiers)


# we plan to use it only for small evaluation datasets
def augment(output_prefix: str, aug_modifier: str, src: str, trg: str):
    """
    Augment corpus on disk using the OpusTrainer modifier
    """
    if aug_modifier not in modifier_map:
        raise ValueError(f"Invalid modifier {aug_modifier}. Allowed values: {modifier_map.keys()}")

    # file paths for compressed and uncompressed corpus
    uncompressed_src = f"{output_prefix}.{src}"
    uncompressed_trg = f"{output_prefix}.{trg}"
    compressed_src = f"{output_prefix}.{src}.zst"
    compressed_trg = f"{output_prefix}.{trg}.zst"

    corpus = read_corpus_tsv(compressed_src, compressed_trg, uncompressed_src, uncompressed_trg)

    if aug_modifier in ("aug-mix", "aug-inline-noise"):
        # add alignments for inline noise
        # Tags modifier will remove them after processing
        corpus = add_alignments(corpus)

    # The mix modifier is dynamic based on the script properties.
    if aug_modifier == "aug-mix":
        modifier_map["aug-mix"] = build_aug_mix(src, trg)

    modified = []
    for line in corpus:
        # recreate modifier for each line to apply randomization (for typos)
        modifier = modifier_map[aug_modifier]()
        modified += modifier([line])
    write_modified(modified, uncompressed_src, uncompressed_trg)


def read_corpus_tsv(
    compressed_src: str, compressed_trg: str, uncompressed_src: str, uncompressed_trg: str
) -> List[str]:
    """
    Decompress corpus and read to TSV
    """
    if os.path.isfile(uncompressed_src):
        os.remove(uncompressed_src)
    if os.path.isfile(uncompressed_trg):
        os.remove(uncompressed_trg)

    # Decompress the original corpus.
    decompress_file(compressed_src, keep_original=False)
    decompress_file(compressed_trg, keep_original=False)

    # Since this is only used on small evaluation sets, it's fine to load the entire dataset
    # and augmentation into memory rather than streaming it.
    with open(uncompressed_src) as f:
        corpus_src = [line.rstrip("\n") for line in f]
    with open(uncompressed_trg) as f:
        corpus_trg = [line.rstrip("\n") for line in f]

    corpus_tsv = [f"{src_sent}\t{trg_sent}" for src_sent, trg_sent in zip(corpus_src, corpus_trg)]
    return corpus_tsv


def write_modified(modified: List[str], uncompressed_src: str, uncompressed_trg: str):
    """
    Split the modified TSV corpus, write back and compress
    """
    modified_src = "\n".join([line.split("\t")[0] for line in modified]) + "\n"
    modified_trg = "\n".join([line.split("\t")[1] for line in modified]) + "\n"

    with open(uncompressed_src, "w") as f:
        f.write(modified_src)
    with open(uncompressed_trg, "w") as f:
        f.writelines(modified_trg)

    # compress corpus back
    compress_file(uncompressed_src, keep_original=False)
    compress_file(uncompressed_trg, keep_original=False)


def run_import(
    type: str,
    dataset: str,
    output_prefix: str,
    src: str,
    trg: str,
):
    # Parse a dataset identifier to extract importer, augmentation type and dataset name
    # Examples:
    # opus_wikimedia/v20230407
    # opus_ELRC_2922/v1
    # mtdata_EU-eac_forms-1-eng-lit
    # flores_aug-title_devtest
    # sacrebleu_aug-upper-strict_wmt19
    match = re.search(r"^([a-z]*)_(aug[a-z\-]*)?_?(.+)$", dataset)

    if not match:
        raise ValueError(
            f"Invalid dataset name: {dataset}. "
            f"Use the following format: <importer>_<name> or <importer>_<augmentation>_<name>."
        )

    importer = match.group(1)
    aug_modifer = match.group(2)
    name = match.group(3)

    download(Downloader(importer), src, trg, name, Path(output_prefix))

    # TODO: convert everything to Chinese simplified for now when Chinese is the source language
    # TODO: https://github.com/mozilla/firefox-translations-training/issues/896
    if "zh" in (src, trg):
        handle_chinese_parallel(output_prefix, src=src, trg=trg, variant=ChineseType.simplified)

    if aug_modifer:
        logger.info("Running augmentation")
        augment(output_prefix, aug_modifer, src=src, trg=trg)


def main() -> None:
    logger.info(f"Running with arguments: {sys.argv}")
    parser = argparse.ArgumentParser(
        description=__doc__,
        # Preserves whitespace in the help text.
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument("--type", metavar="TYPE", type=str, help="Dataset type: mono or corpus")
    parser.add_argument(
        "--src",
        metavar="SRC",
        type=str,
        help="Source language",
    )
    parser.add_argument(
        "--trg",
        metavar="TRG",
        type=str,
        help="Target language",
    )
    parser.add_argument(
        "--dataset",
        metavar="DATASET",
        type=str,
        help="Full dataset identifier. For example, sacrebleu_aug-upper-strict_wmt19 ",
    )
    parser.add_argument(
        "--output_prefix",
        metavar="OUTPUT_PREFIX",
        type=str,
        help="Write output dataset to a path with this prefix",
    )

    args = parser.parse_args()
    logger.info("Starting dataset import and augmentation.")
    run_import(args.type, args.dataset, args.output_prefix, args.src, args.trg)
    logger.info("Finished dataset import and augmentation.")


if __name__ == "__main__":
    main()
