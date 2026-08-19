#!/usr/bin/env python3
"""
Module:  define.py
Purpose: Tier 0 — the encyclopedia. What a word means, answered instantly and offline.
Author:  LB
Date:    2026-08-14

    python -m orchestrator.define "what is an eigenvalue"

(`-m`, not a path, for the same reason `calc.py` says so: this imports from the `orchestrator`
package, and running it as a plain script puts `orchestrator/` on sys.path instead of the
repo root.)

## What LB asked for, 2026-08-14

> "I want him to be like a giant encyclopedia who can define words, remember formulas,
> conversions etc" — and, separately: **"I am still planning to do my work."**

That second sentence is the design constraint, and it is the more important one. This is a
**reference**, not a solver. Every entry answers *what a word means*; none of them work a
problem. `calc.py` computes arithmetic he says out loud and `convert.py` changes units, but
neither takes a homework question and hands back an answer, and nothing here does either.

## Scope — his FALL 2026 degree audit, math and science only

LB's instruction was to keep "all prerequisite and electives relevant to math and science".
Against the Morgan State audit that resolves to:

| Course | Covers |
|---|---|
| MATH 241 / 242 / 243 | calculus, single and multivariable |
| MATH 340 | differential equations |
| EEGR 331 | applied probability and statistics |
| PHYS 205 / 205L | mechanics |
| PHYS 206 / 206L | electricity, magnetism, waves, optics |
| CHEM 110 / 110L | general chemistry |
| — | linear algebra, geometry, trigonometry (LB's additions) |

General education is **out**. Calculus I is in even though the audit has no row for it,
because it gates MATH 242 and LB's word was "all prerequisite".

## A definition frame is REQUIRED, and that is the whole safety argument

This table holds ~180 single-word triggers. Matching them bare would be a catastrophe for the
tier above it: `classify.py` opens by testing personality **before** subject matter, precisely
so that "what do you think of capacitors" reaches Mr Odd Ball rather than an exam board. A
glossary that fired on a bare "capacitor" would swallow that question three intents earlier
and there would be nothing left to classify.

So `look_up` needs **a frame and a term**: "what is a", "define", "what does X mean", "explain".
"What do you think of capacitors" carries no frame, returns None, and falls through untouched.

This is D38 — *"Tier 0 matches phrases, never bare keywords"* — applied at the point where it
would have hurt most. `tools/verify_define.py` asserts both directions.

## Speakability, and a hard length limit

Every string is read aloud by Piper at ~160 words per minute, so 40 words is about 15 seconds.
D32 already set that ceiling for `formulas.py` and the same number binds here, harder: a
definition is the answer to a small question and it should sound like one.

- No symbols: **pi**, not the Greek letter. **theta**, not the symbol. **squared**, not a
  superscript. `UNSPEAKABLE` is imported from `formulas`, never re-declared.
- No operators. "divided by", never a slash.
- Two sentences is the house style: what it is, then why LB cares.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from orchestrator.formulas import _has_phrase

__all__ = ["Term", "TERMS", "look_up", "keys", "subjects"]


@dataclass(frozen=True)
class Term:
    """One word he can define with no model.

    Args:
        key:     stable id, so fixtures never assert on wording.
        subject: which course this belongs to. Not decoration — `--subject` uses it to prove
                 coverage per course, and a subject with two entries is a visible gap.
        terms:   every spelling the transcript might carry. Matched as whole phrases against
                 already-normalised text, longest first.
        spoken:  exactly what he says. Two short sentences, 40 words maximum.
    """

    key: str
    subject: str
    terms: tuple[str, ...]
    spoken: str


# --------------------------------------------------------------------------------------
# The frames that make a question a definition question.
#
# Ordered longest first for the same reason everything else in this repo is: "what does a
# derivative mean" must be consumed as a frame before the bare "what" in it can be.
#
# `normalise()` has already dropped apostrophes, so "what's" arrives as "whats".
# --------------------------------------------------------------------------------------

_FRAMES: tuple[str, ...] = (
    "what is the definition of", "whats the definition of", "give me the definition of",
    "what does it mean by", "what do you mean by", "what does it mean when",
    "can you define", "could you define", "remind me what",
    "what does", "what do", "what is", "whats", "what are", "what re",
    "define", "definition of", "meaning of", "explain", "describe",
    "tell me about", "tell me what", "whats meant by", "what is meant by",
    "in simple terms", "refresh me on", "remind me about",
)

# Words that sit between the frame and the term and carry nothing: "what is *a* derivative".
_ARTICLES = ("a", "an", "the", "some", "my", "this", "that", "actually", "really", "even",
             "exactly", "basically", "just", "again")

# Longest frame first, so "what is the definition of entropy" is consumed by the long frame
# rather than by the "what is" inside it. Same rule as everything else in this repo.
_FRAMES_SORTED = tuple(sorted(_FRAMES, key=len, reverse=True))

# What may legally follow the term and still leave the question a definition question.
# "what does entropy MEAN", "remind me what a phasor IS", "what is a radian AGAIN".
#
# The term has to END the question, modulo one of these. Adjacency to the frame alone was not
# enough: `tools/verify_define.py` caught "what is my mean time to failure looking like"
# being answered with the definition of a mean, because "mean" does sit right after the
# frame. Requiring the term to finish the sentence is what separates asking what a word means
# from using the word in a longer question.
_TRAILERS = ("mean", "means", "meaning", "is", "are", "was", "do", "does", "again",
             "exactly", "actually", "then", "though", "in simple terms", "for short",
             "stand for", "stands for", "used for", "please")


def _strip_articles(text: str) -> str:
    """Drop leading filler so "a derivative mean" becomes "derivative mean"."""
    words = text.split()
    while words and words[0] in _ARTICLES:
        words.pop(0)
    return " ".join(words)


# --------------------------------------------------------------------------------------
# The glossary.
#
# ORDER MATTERS within a subject, the same rule as router.INTENTS and formulas.FORMULAS:
# the most specific term sits above the more general one it contains. "partial derivative"
# must precede "derivative", "standard deviation" must precede "deviation", and
# "electric field" must precede "field".
#
# The matcher sorts every alias in the whole table by length, so the ordering here is for a
# human reading it. The sort is what actually enforces it — see `look_up`.
# --------------------------------------------------------------------------------------

_CORE_TERMS: tuple[Term, ...] = (

    # ==================================================================== algebra and functions
    Term("function", "algebra", ("function",),
         "A function is a rule that gives exactly one output for every input you feed it. "
         "If one input could give two answers, it is not a function."),
    Term("domain", "algebra", ("domain",),
         "The domain is every input a function is allowed to take. "
         "You lose values that would divide by zero or take a square root of a negative."),
    Term("range_fn", "algebra", ("range of a function", "range"),
         "The range is every output a function can actually produce. "
         "The domain is what goes in, the range is what comes out."),
    Term("logarithm", "algebra", ("logarithm", "log", "logs"),
         "A logarithm asks what power you raise the base to, to get your number. "
         "Log base 10 of 1000 is 3. It turns multiplying into adding."),
    Term("natural_log", "algebra", ("natural log", "natural logarithm", "ln"),
         "The natural log is a logarithm with base e, about 2.718. "
         "It shows up everywhere because e to the x is its own derivative."),
    Term("exponential", "algebra", ("exponential", "exponential function"),
         "An exponential function has the variable in the exponent, like 2 to the x. "
         "It grows by a constant factor rather than a constant amount."),
    Term("asymptote", "algebra", ("asymptote",),
         "An asymptote is a line a curve gets arbitrarily close to but never reaches. "
         "Vertical ones come from dividing by zero, horizontal ones from the limit at infinity."),
    Term("polynomial", "algebra", ("polynomial",),
         "A polynomial is a sum of terms, each a number times the variable to a whole power. "
         "Its degree is the highest power in it."),
    Term("factorial", "algebra", ("factorial",),
         "A factorial multiplies every whole number from 1 up to your number. "
         "5 factorial is 120. It counts the ways to order 5 things."),
    Term("complex_number", "algebra", ("complex number", "imaginary number", "imaginary unit"),
         "A complex number has a real part and an imaginary part, written a plus b j. "
         "j is the square root of minus 1, and engineers write j because i is current."),
    Term("polar_form", "algebra", ("polar form", "rectangular form"),
         "Rectangular form is real plus imaginary. Polar form is a magnitude and an angle. "
         "Polar makes multiplying easy, rectangular makes adding easy."),
    Term("eulers_formula", "algebra", ("eulers formula", "euler formula"),
         "Euler's formula says e to the j theta equals cosine theta plus j sine theta. "
         "It is why a rotating vector and a sine wave are the same object."),
    Term("phasor", "algebra", ("phasor",),
         "A phasor is a complex number holding a sine wave's amplitude and phase, "
         "with the frequency left out because every signal in the circuit shares it."),

    # ==================================================================== geometry
    Term("theorem_pythagoras", "geometry", ("pythagorean theorem", "pythagoras"),
         "In a right triangle, the two short sides squared and added give the "
         "hypotenuse squared. It is also just the distance formula in disguise."),
    Term("hypotenuse", "geometry", ("hypotenuse",),
         "The hypotenuse is the longest side of a right triangle, "
         "always the one opposite the right angle."),
    Term("perimeter", "geometry", ("perimeter",),
         "The perimeter is the distance all the way around a shape. "
         "For a circle it has its own name, the circumference."),
    Term("circumference", "geometry", ("circumference",),
         "The circumference is the distance around a circle. "
         "It is 2 pi times the radius, or pi times the diameter."),
    Term("radius", "geometry", ("radius", "diameter"),
         "The radius runs from the centre of a circle to its edge. "
         "The diameter runs all the way across, so it is twice the radius."),
    Term("chord", "geometry", ("chord", "secant line"),
         "A chord is a straight line joining two points on a circle. "
         "The longest chord any circle has is its diameter."),
    Term("tangent_line", "geometry", ("tangent line",),
         "A tangent line touches a curve at one point and matches its slope there. "
         "Finding that slope is the entire point of a derivative."),
    Term("arc_length", "geometry", ("arc length", "arc"),
         "An arc is a piece of a circle's edge. Its length is the radius times the angle "
         "in radians, which is the reason radians exist."),
    Term("sector", "geometry", ("sector",),
         "A sector is a wedge of a circle, like a slice of pie. "
         "Its area is a half r squared times the angle in radians."),
    Term("congruent", "geometry", ("congruent", "similar triangles", "similar"),
         "Congruent shapes are identical. Similar shapes have the same angles but different "
         "sizes, so their sides are all in one ratio."),
    Term("polygon", "geometry", ("polygon", "quadrilateral"),
         "A polygon is a closed shape made of straight sides. "
         "The interior angles of one with n sides add up to n minus 2, times 180 degrees."),
    Term("volume", "geometry", ("volume",),
         "Volume is how much space a solid takes up, measured in cubic units. "
         "A sphere is four thirds pi r cubed, a cylinder is pi r squared times height."),
    Term("centroid", "geometry", ("centroid", "center of mass", "centre of mass"),
         "The centroid is a shape's average position, the point it would balance on. "
         "For a triangle it is where the three medians cross."),

    # ==================================================================== trigonometry
    Term("sohcahtoa", "trigonometry", ("sohcahtoa", "soh cah toa"),
         "Sine is opposite over hypotenuse, cosine is adjacent over hypotenuse, "
         "tangent is opposite over adjacent. Right triangles only."),
    Term("sine", "trigonometry", ("sine", "sin"),
         "Sine is the vertical coordinate of a point going round the unit circle. "
         "In a right triangle it is the opposite side over the hypotenuse."),
    Term("cosine", "trigonometry", ("cosine", "cos"),
         "Cosine is the horizontal coordinate of a point going round the unit circle. "
         "It is sine shifted by 90 degrees, which is why they always appear together."),
    Term("tangent_trig", "trigonometry", ("tangent",),
         "Tangent is sine over cosine, and in a right triangle it is opposite over adjacent. "
         "It blows up at 90 degrees, where the cosine is zero."),
    Term("unit_circle", "trigonometry", ("unit circle",),
         "The unit circle is a circle of radius 1 centred on the origin. "
         "Every point on it is cosine of the angle, then sine of the angle."),
    Term("radian", "trigonometry", ("radian", "radians"),
         "A radian is the angle where the arc length equals the radius. "
         "There are 2 pi in a full turn, so 1 radian is about 57.3 degrees."),
    Term("amplitude", "trigonometry", ("amplitude",),
         "Amplitude is how far a wave swings from its centre line to its peak. "
         "Peak to peak is twice that, and people mix the two up constantly."),
    Term("period", "trigonometry", ("period",),
         "The period is how long one full cycle takes. "
         "It is 1 over the frequency, so 60 hertz has a period of about 16.7 milliseconds."),
    Term("phase", "trigonometry", ("phase", "phase shift", "phase angle"),
         "Phase is where in its cycle a wave starts, measured as an angle. "
         "Two waves out of phase by 180 degrees cancel each other out."),
    Term("pythagorean_identity", "trigonometry", ("pythagorean identity", "trig identity"),
         "Sine squared plus cosine squared equals 1, for every angle. "
         "It is the Pythagorean theorem written on the unit circle."),
    Term("law_of_sines", "trigonometry", ("law of sines", "sine rule"),
         "Each side over the sine of its opposite angle gives the same number, in any "
         "triangle. Use it when you know an angle and the side facing it."),
    Term("law_of_cosines", "trigonometry", ("law of cosines", "cosine rule"),
         "c squared equals a squared plus b squared, minus 2 a b cosine C. "
         "It is the Pythagorean theorem for triangles that are not right angled."),
    Term("inverse_trig", "trigonometry", ("arctangent", "arcsine", "arccosine", "inverse trig"),
         "Inverse trig functions run backwards, from a ratio to the angle that made it. "
         "Arctangent of 1 is 45 degrees."),

    # ==================================================================== calculus
    Term("limit", "calculus", ("limit",),
         "A limit is the value a function heads toward as the input approaches something, "
         "whether or not it ever arrives. Every idea in calculus is built on one."),
    Term("continuity", "calculus", ("continuity", "continuous"),
         "A function is continuous where you can draw it without lifting your pen. "
         "Formally, the limit exists and equals the value there."),
    Term("partial_derivative", "calculus", ("partial derivative", "partial derivatives"),
         "A partial derivative differentiates with respect to one variable and treats every "
         "other one as a constant. It is how you handle a surface instead of a curve."),
    Term("derivative", "calculus", ("derivative", "differentiate", "differentiation"),
         "A derivative is the instantaneous rate of change, the slope of the tangent line. "
         "Position differentiates to velocity, velocity to acceleration."),
    Term("chain_rule", "calculus", ("chain rule",),
         "For a function inside a function, differentiate the outside and multiply by the "
         "derivative of the inside. It is the rule you will use most and forget most."),
    Term("product_rule", "calculus", ("product rule",),
         "The derivative of a product is the first times the derivative of the second, "
         "plus the second times the derivative of the first."),
    Term("quotient_rule", "calculus", ("quotient rule",),
         "Low d high, minus high d low, all over low squared. "
         "If you can rewrite it as a product, the product rule is usually less painful."),
    Term("implicit_diff", "calculus", ("implicit differentiation",),
         "Differentiate both sides with respect to x even when y is not isolated, "
         "applying the chain rule to every y, then solve for d y d x."),
    Term("definite_integral", "calculus", ("definite integral", "definite"),
         "A definite integral has limits and gives a number, the signed area under the curve "
         "between them. An indefinite one gives a function and a plus C."),
    Term("integral", "calculus", ("integral", "integrate", "integration", "antiderivative"),
         "An integral adds up infinitely many infinitely thin pieces. "
         "It is the area under a curve, and it undoes a derivative."),
    Term("riemann_sum", "calculus", ("riemann sum",),
         "A Riemann sum approximates area with rectangles under the curve. "
         "Let their width go to zero and you have the definition of the integral."),
    Term("ftc", "calculus", ("fundamental theorem of calculus",),
         "The fundamental theorem says differentiation and integration undo each other, "
         "and it is why you can evaluate an area by plugging into an antiderivative."),
    Term("integration_by_parts", "calculus", ("integration by parts",),
         "Integral of u d v equals u v minus the integral of v d u. "
         "It is the product rule run backwards, for products you cannot integrate directly."),
    Term("taylor_series", "calculus", ("taylor series", "maclaurin series"),
         "A Taylor series rebuilds a function as an infinite polynomial from its derivatives "
         "at one point. It is why a calculator can find a sine at all."),
    Term("convergence", "calculus", ("convergence", "converge", "diverge", "divergent"),
         "A series converges if its running total settles on a finite number, "
         "and diverges if it does not. Terms going to zero is necessary but not enough."),
    Term("gradient", "calculus", ("gradient", "del operator", "nabla"),
         "The gradient collects the partial derivatives into a vector. "
         "It points in the direction of steepest increase, and its length is that steepness."),
    Term("divergence", "calculus", ("divergence",),
         "Divergence measures how much a vector field flows out of a point. "
         "Positive means a source, negative means a sink."),
    Term("curl", "calculus", ("curl",),
         "Curl measures how much a vector field rotates around a point. "
         "Drop a paddle wheel in the field, and curl is how fast it spins."),
    Term("line_integral", "calculus", ("line integral", "surface integral", "flux integral"),
         "A line integral adds a quantity up along a path rather than along an axis. "
         "It is how work and circulation are actually defined."),
    Term("jacobian", "calculus", ("jacobian",),
         "The Jacobian is the matrix of all first partial derivatives. "
         "Its determinant is the factor a change of variables stretches area or volume by."),
    Term("lagrange_multiplier", "calculus", ("lagrange multiplier", "lagrange multipliers"),
         "Lagrange multipliers optimise a function subject to a constraint by setting its "
         "gradient parallel to the constraint's gradient."),
    Term("related_rates", "calculus", ("related rates",),
         "Related rates link how fast two quantities change through an equation connecting "
         "them. Differentiate the relationship with respect to time, then solve."),

    # ==================================================================== differential equations
    Term("ode", "diffeq", ("differential equation", "ordinary differential equation", "ode"),
         "A differential equation relates a function to its own derivatives. "
         "Solving it means finding the function, not a number."),
    Term("order_deq", "diffeq", ("order of a differential equation",),
         "The order is the highest derivative that appears. "
         "An R L C circuit gives you a second order equation, because it stores energy twice."),
    Term("homogeneous", "diffeq", ("homogeneous", "particular solution",
                                   "complementary solution"),
         "A homogeneous equation has zero on the right side. "
         "The full answer is that solution plus a particular one that matches the input."),
    Term("separable", "diffeq", ("separable", "separation of variables"),
         "Separable means you can get all the y terms on one side and all the x terms on the "
         "other, then integrate both sides. It is the first method you will learn."),
    Term("integrating_factor", "diffeq", ("integrating factor",),
         "An integrating factor is a multiplier that turns a first order linear equation into "
         "a product rule you can integrate directly. It is e to the integral of P."),
    Term("characteristic_equation", "diffeq", ("characteristic equation", "auxiliary equation"),
         "Substitute e to the r t and the differential equation becomes a polynomial in r. "
         "Its roots tell you whether the answer decays, oscillates, or both."),
    Term("initial_value", "diffeq", ("initial value problem", "initial condition",
                                     "boundary condition"),
         "An initial condition pins down the arbitrary constants a general solution carries. "
         "An n th order equation needs n of them."),
    Term("laplace_transform", "diffeq", ("laplace transform",),
         "The Laplace transform turns a differential equation in time into an algebra problem "
         "in s. You solve the algebra, then transform back."),
    Term("superposition", "diffeq", ("superposition",),
         "In a linear system, the response to several inputs is the sum of the responses to "
         "each one alone. It is what makes almost every technique you learn legal."),
    Term("damping", "diffeq", ("damping", "damped", "underdamped", "overdamped",
                               "critically damped"),
         "Damping is how fast oscillation dies away. Underdamped rings, overdamped crawls, "
         "and critically damped settles fastest without overshooting."),
    Term("eigenfunction", "diffeq", ("steady state", "transient"),
         "The transient is the part of the response that dies out; the steady state is what "
         "is left once it has. Both come from the same equation."),

    # ==================================================================== linear algebra
    Term("matrix", "linear_algebra", ("matrix", "matrices"),
         "A matrix is a rectangular grid of numbers that acts on vectors. "
         "Multiplying by one rotates, scales or shears whatever you feed it."),
    Term("determinant", "linear_algebra", ("determinant",),
         "The determinant is one number saying how much a matrix scales area or volume. "
         "Zero means it squashes space flat, so it has no inverse."),
    Term("inverse_matrix", "linear_algebra", ("inverse matrix", "matrix inverse", "singular"),
         "The inverse undoes what a matrix did. "
         "A matrix with determinant zero is singular and has no inverse at all."),
    Term("transpose", "linear_algebra", ("transpose",),
         "Transposing flips a matrix over its diagonal, turning rows into columns. "
         "It is written with a capital T."),
    Term("eigenvalue", "linear_algebra", ("eigenvalue", "eigenvalues", "eigenvector",
                                          "eigenvectors"),
         "An eigenvector is a direction a matrix does not rotate, only stretches. "
         "The eigenvalue is how much it stretches by. They set every natural frequency."),
    Term("rank", "linear_algebra", ("rank",),
         "The rank is how many independent directions a matrix actually spans. "
         "Less than full rank means information is being lost."),
    Term("null_space", "linear_algebra", ("null space", "kernel"),
         "The null space is every vector the matrix sends to zero. "
         "If it holds anything but zero, the system has infinitely many solutions."),
    Term("linear_independence", "linear_algebra", ("linearly independent",
                                                   "linear independence", "span", "basis"),
         "Vectors are independent when none is a combination of the others. "
         "An independent set that spans the space is a basis."),
    Term("dot_product", "linear_algebra", ("dot product", "scalar product"),
         "The dot product multiplies matching components and adds them, giving one number. "
         "It is zero exactly when the two vectors are perpendicular."),
    Term("cross_product", "linear_algebra", ("cross product", "vector product"),
         "The cross product gives a vector perpendicular to both inputs, "
         "with length equal to the area of the parallelogram they make."),
    Term("cramers_rule", "linear_algebra", ("cramers rule", "cramer rule"),
         "Cramer's rule solves a linear system with determinants, one per unknown. "
         "It is elegant for two or three equations and hopeless beyond that."),
    Term("orthogonal", "linear_algebra", ("orthogonal", "orthonormal"),
         "Orthogonal means perpendicular, so the dot product is zero. "
         "Orthonormal adds that every vector has length 1."),

    # ==================================================================== probability and statistics
    Term("mean", "statistics", ("mean", "average"),
         "The mean is the total divided by how many there are. "
         "It gets dragged around by outliers, which is when you want the median instead."),
    Term("median", "statistics", ("median", "mode"),
         "The median is the middle value once you sort them. "
         "The mode is the one that appears most often."),
    Term("standard_deviation", "statistics", ("standard deviation",),
         "Standard deviation is the typical distance from the mean, "
         "in the same units as your data. It is the square root of the variance."),
    Term("variance", "statistics", ("variance",),
         "Variance is the average squared distance from the mean. "
         "Squaring is what keeps it from cancelling to zero, but it also squares the units."),
    Term("random_variable", "statistics", ("random variable",),
         "A random variable assigns a number to each outcome of an experiment. "
         "Discrete ones you can count; continuous ones you measure."),
    Term("expected_value", "statistics", ("expected value", "expectation"),
         "The expected value is the long run average, each outcome weighted by its "
         "probability. It need not be a value the variable can actually take."),
    Term("pdf", "statistics", ("probability density function", "density function"),
         "A density function gives probability per unit of x, not probability itself. "
         "You get a probability by taking the area under it over an interval."),
    Term("cdf", "statistics", ("cumulative distribution function",),
         "The cumulative distribution gives the probability of being at or below a value. "
         "It runs from 0 to 1 and never goes down."),
    Term("normal_distribution", "statistics", ("normal distribution", "gaussian",
                                               "bell curve"),
         "The normal distribution is the bell curve, set by its mean and standard deviation. "
         "About 68 percent lands within one deviation, and 95 within two."),
    Term("binomial", "statistics", ("binomial distribution", "binomial"),
         "The binomial counts successes in a fixed number of independent yes or no trials, "
         "each with the same probability."),
    Term("poisson", "statistics", ("poisson distribution", "poisson"),
         "The Poisson counts how many rare events happen in a fixed window, "
         "given an average rate. Its mean and its variance are the same number."),
    Term("conditional_probability", "statistics", ("conditional probability",),
         "Conditional probability is the chance of A given that B already happened. "
         "It is the probability of both, divided by the probability of B."),
    Term("bayes", "statistics", ("bayes theorem", "bayes rule", "bayes"),
         "Bayes' theorem flips a conditional round, turning the probability of B given A "
         "into the probability of A given B. It is how evidence updates a belief."),
    Term("independence", "statistics", ("independent events", "statistical independence"),
         "Two events are independent when one happening tells you nothing about the other, "
         "so the probability of both is just the two multiplied."),
    Term("central_limit", "statistics", ("central limit theorem",),
         "Sample means come out normally distributed no matter what the original "
         "distribution looked like, once the samples are big enough. It is why the bell curve is everywhere."),
    Term("confidence_interval", "statistics", ("confidence interval",),
         "A confidence interval is a range built so that, over many repeats, "
         "95 percent of the intervals you build would contain the true value."),
    Term("p_value", "statistics", ("p value", "null hypothesis", "hypothesis test"),
         "A p value is the chance of seeing data this extreme if the null hypothesis were "
         "true. Small means surprising, not automatically important."),
    Term("correlation", "statistics", ("correlation", "correlation coefficient"),
         "Correlation measures how tightly two variables track each other, from minus 1 to "
         "plus 1. It says nothing at all about one causing the other."),
    Term("regression", "statistics", ("linear regression", "least squares", "regression"),
         "Regression fits the line that minimises the total squared vertical distance to your "
         "points. That is what least squares means."),
    Term("permutation", "statistics", ("permutation", "combination"),
         "A permutation counts arrangements where order matters. "
         "A combination counts selections where it does not."),

    # ==================================================================== physics: mechanics
    Term("scalar_vector", "physics_mechanics", ("scalar", "vector"),
         "A scalar is just a size, like mass or temperature. "
         "A vector has a size and a direction, like velocity or force."),
    Term("displacement", "physics_mechanics", ("displacement",),
         "Displacement is the straight line from start to finish, with direction. "
         "Distance is how far you actually travelled, and it is never smaller."),
    Term("velocity", "physics_mechanics", ("velocity", "speed"),
         "Speed is how fast, velocity is how fast and which way. "
         "Velocity is the derivative of position with respect to time."),
    Term("acceleration", "physics_mechanics", ("acceleration",),
         "Acceleration is the rate of change of velocity. "
         "Changing direction at constant speed is still accelerating."),
    Term("newtons_laws", "physics_mechanics", ("newtons laws", "newtons first law",
                                               "newtons second law", "newtons third law"),
         "First, things keep doing what they are doing unless a force acts. "
         "Second, force equals mass times acceleration. Third, forces come in equal pairs."),
    Term("inertia", "physics_mechanics", ("inertia",),
         "Inertia is an object's resistance to having its motion changed. "
         "Mass is the measure of it."),
    Term("friction", "physics_mechanics", ("friction", "coefficient of friction"),
         "Friction opposes sliding and is the coefficient times the normal force. "
         "Static friction is stronger than kinetic, which is why things start with a jerk."),
    Term("work", "physics_mechanics", ("work",),
         "Work is force times distance moved along that force. "
         "Push on a wall all day and you have done no work at all."),
    Term("kinetic_energy", "physics_mechanics", ("kinetic energy",),
         "Kinetic energy is a half m v squared, the energy of motion. "
         "Double the speed and you quadruple it."),
    Term("potential_energy", "physics_mechanics", ("potential energy",),
         "Potential energy is stored energy waiting to be released, "
         "like m g h for height or a half k x squared for a spring."),
    Term("conservation_energy", "physics_mechanics", ("conservation of energy",),
         "Energy is never created or destroyed, only moved between forms. "
         "Add up kinetic and potential before and after, and they match."),
    Term("power_mech", "physics_mechanics", ("power",),
         "Power is the rate of doing work, in joules per second, which is watts. "
         "It is how fast the energy moves, not how much there is."),
    Term("momentum", "physics_mechanics", ("momentum",),
         "Momentum is mass times velocity, and in any collision the total is conserved. "
         "Energy is only conserved if the collision is elastic."),
    Term("impulse", "physics_mechanics", ("impulse",),
         "Impulse is force times the time it acts for, and it equals the change in momentum. "
         "It is why airbags work: same change, spread over longer, so less force."),
    Term("collision", "physics_mechanics", ("elastic collision", "inelastic collision"),
         "Momentum is conserved in every collision. "
         "Kinetic energy is only conserved in an elastic one; inelastic ones lose it to heat."),
    Term("torque", "physics_mechanics", ("torque", "moment"),
         "Torque is a twisting force, equal to force times the perpendicular distance to the "
         "pivot. A longer wrench gives more torque for the same push."),
    Term("moment_of_inertia", "physics_mechanics", ("moment of inertia",),
         "Moment of inertia is rotational mass, and it depends on how far the mass sits from "
         "the axis. Spread it out and the same object is harder to spin."),
    Term("angular_velocity", "physics_mechanics", ("angular velocity", "angular acceleration"),
         "Angular velocity is how fast something rotates, in radians per second. "
         "Every straight line formula has a rotational twin using it."),
    Term("centripetal", "physics_mechanics", ("centripetal", "centripetal force"),
         "Centripetal force points at the centre of a circular path and is what keeps the "
         "object turning. It is m v squared over r."),
    Term("equilibrium", "physics_mechanics", ("equilibrium", "static equilibrium"),
         "Equilibrium is when the forces and the torques both add to zero. "
         "It does not mean at rest, it means not accelerating."),
    Term("hookes_law", "physics_mechanics", ("hookes law", "spring constant"),
         "A spring pushes back proportionally to how far you stretch it, F equals minus k x. "
         "k is the spring constant, in newtons per meter."),
    Term("shm", "physics_mechanics", ("simple harmonic motion",),
         "Simple harmonic motion is what you get when the restoring force is proportional to "
         "displacement. The result is a sine wave in time."),
    Term("resonance_phys", "physics_mechanics", ("resonance",),
         "Resonance is driving a system at its own natural frequency, so each push adds to "
         "the last and the amplitude builds."),

    # ==================================================================== physics: E and M, waves
    # bare "charge" added 2026-08-15: "what is charge" returned nothing to an EE student while
    # "what is electric charge" worked. The frame gate makes the bare word safe — the term has
    # to END the question, so "how much do you charge" and "my phone is charging" do not match.
    Term("charge", "physics_em", ("electric charge", "charge"),
         "Charge is the property that makes particles push or pull on each other. "
         "It comes in whole multiples of the elementary charge, and it is conserved."),
    Term("coulombs_law", "physics_em", ("coulombs law", "coulomb law"),
         "Coulomb's law says the force between two charges is k q one q two over r squared. "
         "It is gravity's formula with charge in place of mass, and it can repel."),
    Term("electric_field", "physics_em", ("electric field",),
         "An electric field is the force per unit charge at a point, in volts per meter. "
         "It points the way a positive charge would be pushed."),
    Term("electric_potential", "physics_em", ("electric potential", "potential difference"),
         "Potential is the energy per unit charge at a point, in volts. "
         "Potential difference between two points is what actually drives a current."),
    Term("gauss_law", "physics_em", ("gausss law", "gauss law"),
         "Gauss's law says the electric flux out of a closed surface is the charge inside "
         "divided by epsilon nought. Symmetry makes hard fields easy with it."),
    Term("dielectric", "physics_em", ("dielectric", "permittivity"),
         "A dielectric is an insulator that polarises in a field, which weakens the field "
         "inside it and so raises a capacitor's capacitance."),
    Term("resistivity", "physics_em", ("resistivity", "conductivity"),
         "Resistivity is a material property; resistance also depends on the shape. "
         "Resistance is resistivity times length over cross sectional area."),
    Term("emf", "physics_em", ("electromotive force", "emf"),
         "EMF is the energy per unit charge a source supplies, measured in volts. "
         "It is not really a force, which is a naming accident everyone is stuck with."),
    Term("magnetic_field", "physics_em", ("magnetic field",),
         "A magnetic field is produced by moving charge and acts only on moving charge. "
         "It is measured in tesla, and it curls around a current rather than pointing out."),
    Term("lorentz_force", "physics_em", ("lorentz force",),
         "The magnetic force on a moving charge is q v B sine theta, and it is perpendicular "
         "to both the velocity and the field. So it turns things without speeding them up."),
    Term("magnetic_flux", "physics_em", ("magnetic flux", "flux"),
         "Flux is how much field passes through a surface, field times area times the cosine "
         "of the angle. Measured in webers."),
    Term("faradays_law", "physics_em", ("faradays law", "faraday law", "induction"),
         "A changing magnetic flux through a loop induces a voltage in it. "
         "The faster the change, the bigger the voltage. That is every generator and transformer."),
    Term("lenz_law", "physics_em", ("lenzs law", "lenz law"),
         "The induced current always flows so as to oppose the change that made it. "
         "It is conservation of energy wearing a minus sign."),
    Term("inductance", "physics_em", ("inductance", "self inductance"),
         "Inductance is how strongly a coil opposes a change in its own current, in henries. "
         "It is the magnetic mirror of capacitance."),
    Term("em_wave", "physics_em", ("electromagnetic wave", "electromagnetic radiation"),
         "An electromagnetic wave is an electric and a magnetic field regenerating each other "
         "as they travel. In vacuum they all move at the speed of light."),
    Term("maxwells_equations", "physics_em", ("maxwells equations", "maxwell equations"),
         "Four equations covering all of classical electromagnetism: Gauss for electricity, "
         "Gauss for magnetism, Faraday, and Ampere with Maxwell's correction."),
    Term("refraction", "physics_em", ("refraction", "snells law", "index of refraction"),
         "Light bends when it changes speed entering a new material. "
         "Snell's law says n one sine theta one equals n two sine theta two."),
    Term("reflection", "physics_em", ("reflection", "total internal reflection"),
         "The angle of reflection equals the angle of incidence. "
         "Past a critical angle, light in a dense medium reflects completely instead of leaving."),
    Term("diffraction", "physics_em", ("diffraction",),
         "Diffraction is waves spreading out after passing an edge or a slit. "
         "It gets obvious once the opening is close to the wavelength."),
    Term("interference", "physics_em", ("interference", "constructive interference",
                                        "destructive interference"),
         "Two waves add where they overlap. In step they reinforce, half a cycle apart they "
         "cancel. That is constructive and destructive interference."),
    Term("polarization", "physics_em", ("polarization", "polarisation"),
         "Polarization is the direction the electric field of a wave oscillates in. "
         "A polarising filter passes only one direction."),
    Term("doppler", "physics_em", ("doppler effect", "doppler shift"),
         "Relative motion changes the frequency you observe. "
         "Approaching raises the pitch, receding lowers it."),

    # ==================================================================== chemistry
    Term("atom", "chemistry", ("atom", "atomic structure"),
         "An atom is a nucleus of protons and neutrons with electrons around it. "
         "The proton count is the element; the electron count decides the chemistry."),
    Term("isotope", "chemistry", ("isotope", "isotopes"),
         "Isotopes are atoms of one element with different numbers of neutrons. "
         "Same chemistry, different mass, and sometimes radioactive."),
    Term("mole", "chemistry", ("mole",),
         "A mole is Avogadro's number of anything, about 6.022 times 10 to the 23. "
         "It is a counting word, exactly like a dozen, just much bigger."),
    Term("molar_mass", "chemistry", ("molar mass", "molecular weight", "atomic mass"),
         "Molar mass is the grams in one mole of a substance. "
         "Read it straight off the periodic table and add the atoms up."),
    Term("stoichiometry", "chemistry", ("stoichiometry",),
         "Stoichiometry uses the coefficients in a balanced equation as a ratio "
         "to work out how much reacts with how much."),
    Term("molarity", "chemistry", ("molarity", "concentration"),
         "Molarity is moles of solute per liter of solution. "
         "Note it is per liter of solution, not per liter of solvent."),
    Term("ion", "chemistry", ("ion", "cation", "anion"),
         "An ion is an atom that has lost or gained electrons, so it carries a charge. "
         "Cations are positive, anions are negative."),
    Term("covalent_bond", "chemistry", ("covalent bond", "ionic bond", "chemical bond"),
         "A covalent bond shares electrons between atoms. "
         "An ionic bond transfers them outright, then the opposite charges attract."),
    Term("electronegativity", "chemistry", ("electronegativity",),
         "Electronegativity is how hard an atom pulls on shared electrons. "
         "A big difference across a bond makes it ionic; a small one makes it polar."),
    Term("valence", "chemistry", ("valence electron", "valence electrons", "valence"),
         "Valence electrons are the outermost ones, and they do all the bonding. "
         "In silicon there are four, which is the whole reason semiconductors work."),
    Term("oxidation", "chemistry", ("oxidation", "reduction", "redox"),
         "Oxidation is losing electrons, reduction is gaining them, and they always happen "
         "together. Remember OIL RIG: oxidation is loss, reduction is gain."),
    Term("acid_base", "chemistry", ("acid", "base", "alkaline"),
         "An acid donates protons, a base accepts them. "
         "In water that shows up as more hydrogen ions or more hydroxide ions."),
    Term("ph", "chemistry", ("ph", "ph scale"),
         "pH is minus the log of the hydrogen ion concentration. "
         "7 is neutral, below is acidic, above is basic, and each step is a factor of ten."),
    Term("buffer", "chemistry", ("buffer", "buffer solution"),
         "A buffer resists pH change, using a weak acid and its conjugate base together "
         "to soak up whatever you add."),
    Term("ideal_gas", "chemistry", ("ideal gas law", "ideal gas"),
         "P V equals n R T. It assumes molecules have no volume and do not attract, "
         "which holds well at low pressure and high temperature."),
    Term("chemical_equilibrium", "chemistry", ("chemical equilibrium", "le chateliers"),
         "At equilibrium the forward and reverse reactions run at the same rate. "
         "Disturb it and it shifts to partly undo the disturbance."),
    Term("catalyst", "chemistry", ("catalyst", "activation energy"),
         "A catalyst lowers the activation energy so a reaction goes faster, "
         "and comes out unchanged. It never shifts where equilibrium ends up."),
    Term("exothermic", "chemistry", ("exothermic", "endothermic", "enthalpy"),
         "Exothermic releases heat, endothermic absorbs it. "
         "Enthalpy change is negative for the first and positive for the second."),
    Term("entropy", "chemistry", ("entropy",),
         "Entropy measures how many ways the energy can be arranged, loosely how disordered "
         "things are. The total entropy of the universe only ever increases."),
    Term("periodic_trends", "chemistry", ("periodic table", "periodic trends"),
         "Going right across a period atoms get smaller and grip electrons harder. "
         "Going down a group they get bigger and let go more easily."),
)


# --------------------------------------------------------------------------------------
# The electrical engineering half, kept in its own file.
#
# D43 scoped this table from the math and science rows of LB's degree audit, and those rows do
# not contain his major — so on 2026-08-15 an EE student's encyclopedia answered 9 of 74 EE
# questions and had no entry for "current" or "resistance" (D47). `ee_terms.py` is the fix.
#
# It holds plain tuples rather than `Term` objects and imports nothing, so there is no import
# cycle between the two files whose correctness depends on the order of statements in this one.
# It is content; everything structural stays here.
# --------------------------------------------------------------------------------------

from orchestrator import ee_terms                                        # noqa: E402

TERMS: tuple[Term, ...] = _CORE_TERMS + tuple(Term(*row) for row in ee_terms.EE_ROWS)


# --------------------------------------------------------------------------------------
# Matching.
# --------------------------------------------------------------------------------------

def _alias_order() -> tuple[tuple[str, Term], ...]:
    """Every term spelling paired with its entry, longest phrase first.

    Longest-first across the WHOLE table, not per entry, because the collisions cross
    entries: "partial derivative" lives under `partial_derivative` and "derivative" under
    `derivative`, and matching the short one first would make the specific entry dead code.
    The table's own ordering is for a human; this sort is what enforces it.
    """
    rows = [(alias, term) for term in TERMS for alias in term.terms]
    rows.sort(key=lambda row: (-len(row[0]), row[0]))
    duplicates = [a for a, _ in rows if sum(1 for b, _ in rows if b == a) > 1]
    if duplicates:                       # a duplicate alias makes one entry unreachable
        raise ValueError(f"duplicate term aliases: {sorted(set(duplicates))}")
    return tuple(rows)


_ALIASES = _alias_order()


def look_up(normalised: str) -> Term | None:
    """Define the word this question is asking about, or None.

    A pure function of a string — no clock, no model, no network. Takes text that
    `router.normalise()` has already lowercased, de-punctuated and whitespace-collapsed.

    Args:
        normalised: the transcript, normalised.

    Returns:
        The matching Term, or None.

    ## The term must sit between the frame and the END of the question

    Not "a frame somewhere and a term somewhere". That was the first version, and it was
    wrong in two ways that two different harnesses caught:

        "what is the airspeed velocity of an unladen swallow"   -> defined velocity
        "what is my mean time to failure looking like"          -> defined mean

    The first has the frame "what is" and contains "velocity"; the second has "mean" sitting
    *immediately* after the frame, so requiring adjacency alone did not save it either. Both
    had to fall through — the swallow is a `verify_stt.py` fixture that must stay `unknown`
    and `handled=False`, or Phase 2 never gets the question.

    What actually separates asking what a word means from using the word in a longer sentence
    is that a definition question **ends on the term**: "what is an eigenvalue", "define
    entropy", "what does a derivative mean". So the term must be the whole of what follows
    the frame, give or take an article in front and a `_TRAILERS` word behind.

    This is structural rather than a blacklist, which matters: no fixture list would have
    predicted either sentence.
    """
    if not normalised:
        return None
    for frame in _FRAMES_SORTED:
        for match in re.finditer(rf"(?<![a-z0-9]){re.escape(frame)}(?![a-z0-9])", normalised):
            rest = _strip_articles(normalised[match.end():].strip())
            if not rest:
                continue
            for alias, term in _ALIASES:
                if rest == alias:
                    return term
                if rest.startswith(alias + " ") and rest[len(alias):].strip() in _TRAILERS:
                    return term
    return None


def keys() -> tuple[str, ...]:
    return tuple(t.key for t in TERMS)


def subjects() -> dict[str, int]:
    """How many entries each subject has. A subject with two is a visible gap."""
    counts: dict[str, int] = {}
    for term in TERMS:
        counts[term.subject] = counts.get(term.subject, 0) + 1
    return dict(sorted(counts.items()))


def main(argv: list[str] | None = None) -> int:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    if args and args[0] == "--subjects":
        total = 0
        for subject, count in subjects().items():
            print(f"  {subject:22s} {count:3d}")
            total += count
        print(f"  {'TOTAL':22s} {total:3d} entries, "
              f"{sum(len(t.terms) for t in TERMS)} spellings")
        return 0

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    from orchestrator.router import normalise

    questions = args or [
        "what is an eigenvalue",
        "define entropy",
        "what does a derivative mean",
        "explain the law of cosines",
        "what do you think of capacitors",
    ]
    for question in questions:
        hit = look_up(normalise(question))
        print(f"  {question!r:52} -> {hit.key if hit else '(no definition)'}")
        if hit:
            print(f"      {hit.spoken}")
    if not re.search(r"\w", "".join(questions)):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
