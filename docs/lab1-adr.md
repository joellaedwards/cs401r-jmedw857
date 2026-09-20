## ADR-001: NorthStar Platform Foundation

### Status
Accepted

### Context
In the next few months, NorthStar will be creating three separate AI systems: churn scoring, LLM offer generation, and a customer service agent. Each of these systems will deal with a large quantity of data, and we as a company are responsible for making sure that data is protected. Some of this data is particularly sensitive. For example, we will need to train our customer service agent on past interactions with customers. Our customers expect us to keep their information safe. We want to figure out a way to use all the data and technology we have in a safe, but effective way. By building a strong AI platform, we ensure we have safe policies and procedures in place before we even begin building our various AI solutions. 


### Decision
In order to protect our data, we will create a generic IAM role for an ML Engineer. This role will have limited permissions so that all of our data stays safe. We will initialize this role with the base permissions we believe each of our AI solutions will need. As we begin developing these solutions, the permissions needed in each section will be based on the initial permissions of our ML Engineer role. 
We need to be careful about what networks we allow in and out. For this reason, we will create a VPC want our VPC that restricts what comes in. We will make sure nothing from the public internet can connect to Studio. However, we want to be sure the Studio can still reach outward. We chose to use a public subnet paired with an internet gateway to keep costs low. This is a quality we need all of our models to have to ensure our company and its data is protected while still allowing our models to do their jobs. 
We will also need a way to organize all of this data so we know which models are using what data and when. We don't need the customer service agent to know how likely it is a customer will churn. On the other hand, our churn prediction model doesn't need to know what the human escalation rate on the customer service agent is. For this reason, we will store all of the data in different folders within an S3 bucket. Using an S3 bucket will make it easy for our models to access the data, and using folders will keep the different data separate.

### Consequences
#### What this makes easy
This decision makes it easy to build further AI models in the future! By taking time now to set up a good process, we estimate future AI solutions will take off quicker, increasing overall development speed by at least 70%. It leaves us open to extension and closed to modification. In the long run, with AI continuing to grow, we believe this will save us a lot of time.
#### What this makes harder
We are being extremely careful about IAM roles which means some engineers may face frustration of not having all the access they want right when they want it. Waiting on leadership to approve new access can take up to 7 business days. If this happens even 3 times, it will put the entire project back a month (~20 working days in a month, 7 days * 3 occurances = 21 days lost waiting). It will also take time to build out an AI platform. We are estimating the platform will be done in December which pushes off the rest of our projects to the new year.

#### What would cause you to revisit this decision
If we had a critical business case that needed a model built ASAP, we might revisit this decision and build that model before the platform was finished. For example, if our entire customer service department quit tomorrow, we would have an urgent business need to get the customer service project up and running very quickly. 

### Alternative Considered
We considered building each of our forecasted AI solutions completely separate from one another. However, we have seen in the past that when engineers come together to build one solution or template for a solution, the results across the board are better and easier to understand. 
We have decided that although we are adding time now, we will save a lot of time later. If we continue completing 3 AI projects every year, we will save 6 months a year because we don't have to do the same setup every time. Taking 3 months now to save 2 months per project seems like a good sacrifice. 

### AWS Service Selection
- Networking isolation model - using a public subnet paired with an internet gateway and route table will keep our Studio safe and costs low.
- Storage design - Using an S3 bucket will allow data to be available to the SageMaker in AWS. Using folders within it will keep the data for the different AI solutions separate
- Identity model - We chose least privilege so each team can be assured their data is safe from other teams working on similar projects. As a company, using IAM least privilege role lets us keep our customers and their data safe.
- ML development environment - We chose SageMaker studio because it will be easy for our new AI developers to use and because they are already well acquainted with AWS and its tools. 