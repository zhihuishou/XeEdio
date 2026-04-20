package engine

import (
	"context"
	"encoding/json"
	"log/slog"
	"time"

	"ai-video-pipeline/internal/harness"
	"ai-video-pipeline/internal/model"

	"github.com/google/uuid"
)

// PipelineEngine orchestrates the sequential execution of pipeline stages.
type PipelineEngine struct {
	stages []harness.Harness
	logger *slog.Logger
}

// NewPipelineEngine creates a new PipelineEngine with the given stages and logger.
func NewPipelineEngine(stages []harness.Harness, logger *slog.Logger) *PipelineEngine {
	if logger == nil {
		logger = slog.Default()
	}
	return &PipelineEngine{
		stages: stages,
		logger: logger,
	}
}

// Run executes the full pipeline for the given topic and returns a PipelineReport.
// It calls each stage sequentially, passing the output of one stage as input to the next.
// If any stage fails, subsequent stages are marked as "skipped".
func (e *PipelineEngine) Run(ctx context.Context, topic string) model.PipelineReport {
	runID := uuid.New().String()

	e.logger.Info("pipeline started",
		slog.String("run_id", runID),
		slog.String("topic", topic),
	)

	// Build initial input from topic
	pipelineInput := model.PipelineInput{Topic: topic}
	inputBytes, _ := json.Marshal(pipelineInput)

	report := model.PipelineReport{
		RunID:  runID,
		Topic:  topic,
		Stages: make([]model.StageResult, len(e.stages)),
		Status: "success",
	}

	currentInput := inputBytes
	failed := false
	var totalDuration int64

	for i, stage := range e.stages {
		if failed {
			// Mark remaining stages as skipped
			report.Stages[i] = model.StageResult{
				StageName: stage.Name(),
				Status:    "skipped",
				Duration:  0,
			}
			continue
		}

		start := time.Now()
		output, err := stage.Execute(ctx, currentInput)
		duration := time.Since(start).Milliseconds()
		totalDuration += duration

		if err != nil {
			report.Stages[i] = model.StageResult{
				StageName: stage.Name(),
				Status:    "failed",
				Duration:  duration,
				Error:     err.Error(),
			}
			report.Status = "failed"
			failed = true

			e.logger.Error("stage failed",
				slog.String("run_id", runID),
				slog.String("stage", stage.Name()),
				slog.Int64("duration_ms", duration),
				slog.String("error", err.Error()),
			)
		} else {
			report.Stages[i] = model.StageResult{
				StageName: stage.Name(),
				Status:    "success",
				Duration:  duration,
			}
			currentInput = output

			e.logger.Info("stage completed",
				slog.String("run_id", runID),
				slog.String("stage", stage.Name()),
				slog.Int64("duration_ms", duration),
			)
		}
	}

	report.Total = totalDuration

	e.logger.Info("pipeline finished",
		slog.String("run_id", runID),
		slog.String("status", report.Status),
		slog.Int64("total_ms", report.Total),
	)

	return report
}
