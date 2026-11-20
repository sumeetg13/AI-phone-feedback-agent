import FeedbackForm from "./feedback-form";

export default function Home() {
  return (
    <>
      <h1>How was your course?</h1>
      <p className="lede">
        You spent 10 hours learning to get real work done with AI agents. Two minutes of feedback
        helps us make the next cohort&apos;s course better.
      </p>
      <FeedbackForm />
    </>
  );
}
