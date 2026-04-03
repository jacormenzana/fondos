def slice_window(df, start_date, end_date):
    mask = (df["date"] >= start_date) & (df["date"] <= end_date)
    return df.loc[mask].copy()
