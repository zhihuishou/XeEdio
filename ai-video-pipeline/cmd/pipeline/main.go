package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"time"

	"ai-video-pipeline/internal/config"
	"ai-video-pipeline/internal/engine"
	"ai-video-pipeline/internal/harness"
	"ai-video-pipeline/internal/wrapper"
)

func main() {
	// Parse command line flags
	configPath := flag.String("config", "config.yaml", "path to config.yaml")
	// 定义了一个短参数 -c
	flag.StringVar(configPath, "c", "config.yaml", "path to config.yaml (shorthand)")

	topic := flag.String("topic", "", "topic string for video generation (required)")
	flag.StringVar(topic, "t", "", "topic string for video generation (shorthand)")

	title := flag.String("title", "", "optional video title for publishing")

	flag.Parse()

	if *topic == "" {
		fmt.Fprintln(os.Stderr, "error: -topic (-t) is required")
		flag.Usage()
		os.Exit(1)
	}

	// Set up structured logger
	logger := slog.New(slog.NewJSONHandler(os.Stderr, nil))

	// Load configuration
	cfg, err := config.Load(*configPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}

	// Initialize wrappers
	llmClient := wrapper.NewDeepSeekLLMClient(
		cfg.DeepSeek.APIKey,
		cfg.DeepSeek.Endpoint,
		time.Duration(cfg.DeepSeek.Timeout)*time.Second,
	)

	ttsClient := wrapper.NewEdgeTTSClient(
		cfg.EdgeTTS.Voice,
		cfg.EdgeTTS.OutputDir,
	)

	mediaClient := wrapper.NewPexelsMediaClient(
		cfg.Pexels.APIKey,
		cfg.Pexels.OutputDir,
		30*time.Second,
	)

	renderer := wrapper.NewFFmpegRenderer(
		cfg.FFmpeg.Path,
		cfg.FFmpeg.OutputDir,
	)

	publisher, err := harness.NewPublisherFactory(
		cfg.Publish.Platform,
		cfg.Publish,
		30*time.Second,
	)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}

	// Build retry configs from harness configuration
	scriptRetry := harness.RetryConfig{
		MaxRetries:    cfg.Harnesses.Script.MaxRetries,
		RetryInterval: time.Duration(cfg.Harnesses.Script.RetryInterval) * time.Millisecond,
		Timeout:       time.Duration(cfg.Harnesses.Script.Timeout) * time.Second,
	}
	materialRetry := harness.RetryConfig{
		MaxRetries:    cfg.Harnesses.Material.MaxRetries,
		RetryInterval: time.Duration(cfg.Harnesses.Material.RetryInterval) * time.Millisecond,
		Timeout:       time.Duration(cfg.Harnesses.Material.Timeout) * time.Second,
	}
	synthesisRetry := harness.RetryConfig{
		MaxRetries:    cfg.Harnesses.Synthesis.MaxRetries,
		RetryInterval: time.Duration(cfg.Harnesses.Synthesis.RetryInterval) * time.Millisecond,
		Timeout:       time.Duration(cfg.Harnesses.Synthesis.Timeout) * time.Second,
	}
	distributionRetry := harness.RetryConfig{
		MaxRetries:    cfg.Harnesses.Distribution.MaxRetries,
		RetryInterval: time.Duration(cfg.Harnesses.Distribution.RetryInterval) * time.Millisecond,
		Timeout:       time.Duration(cfg.Harnesses.Distribution.Timeout) * time.Second,
	}

	// Initialize harnesses
	scriptHarness := harness.NewScriptHarness(scriptRetry, logger, llmClient)
	materialHarness := harness.NewMaterialHarness(materialRetry, logger, ttsClient, mediaClient)
	synthesisHarness := harness.NewSynthesisHarness(synthesisRetry, logger, renderer)
	distributionHarness := harness.NewDistributionHarness(distributionRetry, logger, publisher)

	// Assemble pipeline engine
	stages := []harness.Harness{scriptHarness, materialHarness, synthesisHarness, distributionHarness}
	pe := engine.NewPipelineEngine(stages, logger)

	// Run the pipeline
	ctx := context.Background()

	// If a title is provided, set it so the distribution stage can use it
	_ = *title // title is available for future use in PipelineInput extension

	report := pe.Run(ctx, *topic)

	// Marshal and print the PipelineReport as JSON to stdout
	reportJSON, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: failed to marshal report: %v\n", err)
		os.Exit(1)
	}
	fmt.Println(string(reportJSON))

	// Exit with appropriate code
	if report.Status != "success" {
		os.Exit(1)
	}
}
