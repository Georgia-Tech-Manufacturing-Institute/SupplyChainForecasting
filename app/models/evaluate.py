def prediction_to_order_plot():
    model_qty = round(preds_combined/pred_amt_test)
    fig, axes = plt.subplots(1,2, sharey=True,sharex=True)
    datasplit = (baseline_qty>80)
    axes[0].set_ylabel("Actual Consumption")
    axes[0].scatter(baseline_qty[datasplit], true_qty[datasplit],
                    s=1, c=df_valid[test_mask].Lookahead[datasplit])
    axes[0].set_xlabel("Waterfall Prediction")
    axes[1].scatter(model_qty[datasplit], true_qty[datasplit],s=1, 
                    c=df_valid[test_mask].Lookahead[datasplit])
    axes[1].set_xlabel("Model Prediction")
    axes[0].set_xlim([-500, 10000])
    axes[0].set_ylim([-500, 10000])