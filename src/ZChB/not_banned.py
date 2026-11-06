from pathlib import Path

import pandas as pd


# === Путь к входному файлу ===
INPUT_FILE = r"/Users/darabon/Desktop/fssp_2026/more3mln_from2000to3390/datanewton/extractpdf_decisions_fin_tax_arbitr_output.xlsx"


def is_filled(series: pd.Series) -> pd.Series:
    """
    Считает ячейку непустой, если:
    - значение не NaN
    - после приведения к строке там не пусто
    """
    return series.notna() & (series.astype(str).str.strip() != "")


def main() -> None:
    input_path = Path(INPUT_FILE)

    # Формируем путь для выходного файла
    output_path = input_path.with_name(
        f"{input_path.stem}_not_banned{input_path.suffix}"
    )

    df = pd.read_excel(input_path)

    columns_to_check = ["Брак по должнику", "Брак по кредитору"]
    existing_columns = [col for col in columns_to_check if col in df.columns]

    if not existing_columns:
        print("Колонки не найдены. Файл сохранен без изменений.")
        df.to_excel(output_path, index=False)
        return

    mask_to_exclude = pd.Series(False, index=df.index)

    for col in existing_columns:
        mask_to_exclude |= is_filled(df[col])

    result = df.loc[~mask_to_exclude].copy()
    result.to_excel(output_path, index=False)

    print(f"Готово.")
    print(f"Исходных строк: {len(df)}")
    print(f"Удалено строк: {mask_to_exclude.sum()}")
    print(f"Осталось строк: {len(result)}")
    print(f"Файл сохранен: {output_path}")


if __name__ == "__main__":
    main()